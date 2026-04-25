"""
Application config — BROKEN.

Anti-patterns deliberately preserved as the "before" state for Activity 9.
The credential strings here are obviously-fake placeholders (the word
"FAKE" is embedded in every one) so secret scanners don't misfire on the
demo, but the *shape* of the bug — secrets in tracked source — is real.

A 2022 GitHub research paper found that on average a real AWS access key
checked into a public repo is harvested by automated scanners in **under
60 seconds**. The cost of one mistake here is full account takeover.

Anti-patterns
-------------
1. Secrets hardcoded as module-level constants in a tracked file.
2. boto3 clients constructed with explicit access_key_id / secret_access_key
   — bypasses IAM roles, ties the binary to a specific human's keys, and
   makes rotation a code change.
3. Startup banner that prints the API key to stdout / logs.
4. No `.gitignore` for `.env` files.
5. No pre-commit / CI scanning to prevent the next leak.
6. The OpenAI client and the S3 client both consume these constants, so a
   key rotation requires editing source and redeploying.

If this file ever lands on GitHub, the immediate response is:
    1. Revoke / rotate every secret listed here.
    2. `git filter-repo` to scrub history (force-push required).
    3. Audit CloudTrail for unauthorized API calls.
    4. Rotate every other secret on the same account.
"""

import boto3
import openai

# Anti-pattern: hardcoded secrets in a tracked file.
OPENAI_API_KEY = "sk-FAKE-DO-NOT-USE-1234567890abcdefghijklmnopqr"
AWS_ACCESS_KEY_ID = "AKIAFAKEEXAMPLE12345"
AWS_SECRET_ACCESS_KEY = "FAKEsecretKeyDONOTUSEexample/abcdefg+12345"
AWS_REGION = "us-east-1"

S3_BUCKET = "fossilrag-prod"

# Anti-pattern: print the secret on startup. CloudWatch ingests this and now
# the key lives in a log retention bucket too.
print(f"Booting with OpenAI key {OPENAI_API_KEY}")

# Anti-pattern: explicit access keys instead of the default credential
# provider chain (which would pick up an IAM role automatically).
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
)

# Same anti-pattern with the OpenAI SDK.
openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
