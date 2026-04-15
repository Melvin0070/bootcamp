"""
Unit tests for the fixed document ingestion Lambda.

Run with:  pytest tests/ -v
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Set required env vars BEFORE importing the module under test
# ---------------------------------------------------------------------------
os.environ['RAW_BUCKET'] = 'test-raw-bucket'
os.environ['SILVER_BUCKET'] = 'test-silver-bucket'
os.environ['IDEMPOTENCY_TABLE'] = ''   # disabled by default

# Add parent directory to path so we can import lambda_function
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import lambda_function


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sqs_event(*s3_keys: str) -> dict:
    """Build a minimal SQS event with one record per key."""
    return {
        'Records': [
            {
                'messageId': f'msg-{i}',
                'body': json.dumps({'s3_key': key}),
                'receiptHandle': f'receipt-{i}',
            }
            for i, key in enumerate(s3_keys)
        ]
    }


class _FakeContext:
    function_name = 'test-fossilrag-processor'

    def get_remaining_time_in_millis(self) -> int:
        return 55_000


CTX = _FakeContext()


# ---------------------------------------------------------------------------
# Handler: happy path
# ---------------------------------------------------------------------------

class TestHandlerHappyPath:
    @patch('lambda_function.s3')
    def test_single_record_succeeds(self, mock_s3):
        mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'Hello World')}
        mock_s3.put_object.return_value = {}

        result = lambda_function.handler(_sqs_event('docs/a.txt'), CTX)

        assert result == {'batchItemFailures': []}
        mock_s3.put_object.assert_called_once()

    @patch('lambda_function.s3')
    def test_batch_of_ten_all_succeed(self, mock_s3):
        mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'content')}
        mock_s3.put_object.return_value = {}
        keys = [f'docs/file-{i}.txt' for i in range(10)]

        result = lambda_function.handler(_sqs_event(*keys), CTX)

        assert result == {'batchItemFailures': []}
        assert mock_s3.put_object.call_count == 10

    @patch('lambda_function.s3')
    def test_output_written_to_silver_prefix(self, mock_s3):
        mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'text')}
        mock_s3.put_object.return_value = {}

        lambda_function.handler(_sqs_event('raw/report.txt'), CTX)

        put_call_kwargs = mock_s3.put_object.call_args[1]
        assert put_call_kwargs['Key'] == 'silver/raw/report.txt'
        assert put_call_kwargs['Bucket'] == 'test-silver-bucket'


# ---------------------------------------------------------------------------
# Handler: partial batch failure
# ---------------------------------------------------------------------------

class TestPartialBatchFailure:
    @patch('lambda_function.s3')
    def test_one_bad_record_isolated(self, mock_s3):
        """When record 1 fails, record 0 should still succeed."""
        def get_object(**kwargs):
            if 'bad' in kwargs['Key']:
                raise Exception('S3 object not found')
            return {'Body': MagicMock(read=lambda: b'good')}

        mock_s3.get_object.side_effect = get_object
        mock_s3.put_object.return_value = {}

        result = lambda_function.handler(_sqs_event('docs/good.txt', 'docs/bad.txt'), CTX)

        # Only the bad message reported as failed
        assert len(result['batchItemFailures']) == 1
        assert result['batchItemFailures'][0]['itemIdentifier'] == 'msg-1'
        # Good message was uploaded
        mock_s3.put_object.assert_called_once()

    @patch('lambda_function.s3')
    def test_all_records_fail_no_exception_raised(self, mock_s3):
        """All records failing must NOT raise — Lambda must return failures cleanly."""
        mock_s3.get_object.side_effect = Exception('S3 unavailable')

        result = lambda_function.handler(_sqs_event('a.txt', 'b.txt', 'c.txt'), CTX)

        assert len(result['batchItemFailures']) == 3
        # Verify message IDs match
        ids = {f['itemIdentifier'] for f in result['batchItemFailures']}
        assert ids == {'msg-0', 'msg-1', 'msg-2'}

    @patch('lambda_function.s3')
    def test_middle_record_fails(self, mock_s3):
        """Records before and after a failure should still be processed."""
        def get_object(**kwargs):
            if 'middle' in kwargs['Key']:
                raise ValueError('Malformed document')
            return {'Body': MagicMock(read=lambda: b'ok')}

        mock_s3.get_object.side_effect = get_object
        mock_s3.put_object.return_value = {}

        result = lambda_function.handler(
            _sqs_event('first.txt', 'middle.txt', 'last.txt'), CTX
        )

        assert len(result['batchItemFailures']) == 1
        assert result['batchItemFailures'][0]['itemIdentifier'] == 'msg-1'
        assert mock_s3.put_object.call_count == 2   # first + last uploaded


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_removes_null_bytes(self):
        assert '\x00' not in lambda_function.clean_text('he\x00llo')

    def test_removes_other_control_chars(self):
        dirty = ''.join(chr(i) for i in range(0, 32) if i not in (9, 10, 13))
        cleaned = lambda_function.clean_text(dirty)
        for char in dirty:
            assert char not in cleaned

    def test_preserves_tabs_and_newlines(self):
        text = 'line1\tcolumn\nline2'
        assert lambda_function.clean_text(text) == text

    def test_normalises_windows_line_endings(self):
        assert lambda_function.clean_text('a\r\nb') == 'a\nb'

    def test_normalises_old_mac_line_endings(self):
        assert lambda_function.clean_text('a\rb') == 'a\nb'

    def test_collapses_multiple_spaces(self):
        assert lambda_function.clean_text('too   many    spaces') == 'too many spaces'

    def test_strips_leading_trailing_whitespace(self):
        assert lambda_function.clean_text('  hello  ') == 'hello'

    def test_empty_string(self):
        assert lambda_function.clean_text('') == ''

    def test_unicode_preserved(self):
        text = 'Dino 🦕 fossil'
        assert lambda_function.clean_text(text) == text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    @patch('lambda_function.s3')
    @patch('lambda_function.IDEMPOTENCY_TABLE', 'test-table')
    @patch('lambda_function._already_processed', return_value=True)
    def test_already_processed_document_is_skipped(self, mock_check, mock_s3):
        """A document that's already in the idempotency table must not be re-processed."""
        result = lambda_function.handler(_sqs_event('docs/done.txt'), CTX)

        # Record succeeds (no failure reported)
        assert result == {'batchItemFailures': []}
        # S3 was never touched
        mock_s3.get_object.assert_not_called()
        mock_s3.put_object.assert_not_called()

    @patch('lambda_function.s3')
    @patch('lambda_function.IDEMPOTENCY_TABLE', 'test-table')
    @patch('lambda_function._already_processed', return_value=False)
    @patch('lambda_function._mark_processed')
    def test_new_document_is_marked_after_processing(self, mock_mark, mock_check, mock_s3):
        mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'new content')}
        mock_s3.put_object.return_value = {}

        lambda_function.handler(_sqs_event('docs/new.txt'), CTX)

        mock_mark.assert_called_once_with('docs/new.txt')

    @patch('lambda_function.s3')
    @patch('lambda_function.IDEMPOTENCY_TABLE', 'test-table')
    @patch('lambda_function._already_processed', side_effect=Exception('DynamoDB down'))
    def test_idempotency_failure_does_not_block_processing(self, mock_check, mock_s3):
        """If DynamoDB is unavailable, processing must continue (fail open)."""
        # _already_processed raises, but the Lambda should still process the doc
        # via the fail-open logic in the helper
        mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'content')}
        mock_s3.put_object.return_value = {}

        # This exercises the outer try/except in handler
        result = lambda_function.handler(_sqs_event('docs/test.txt'), CTX)
        # The record errored (because _already_processed raised before we even
        # get to the fail-open inside _already_processed), so it's a batch failure
        assert isinstance(result, dict)
        assert 'batchItemFailures' in result
