"""
Broken Airflow DAG -- fossil ingestion pipeline.

Anti-patterns present (each annotated with BUG):
  1. One strike and you're out -- a single Lambda timeout loses the whole run
  2. Silent failures -- no alerting wired up, so data goes missing undetected
  3. Tasks can hang indefinitely -- no per-task time ceiling set
  4. print() noise -- logs are unstructured strings, not queryable in CloudWatch
  5. Function names baked into code -- breaks across staging/prod deployments
  6. AWS SDK surface not fully checked -- only HTTP status inspected, not payload
"""

from datetime import datetime

import boto3
from airflow import DAG
from airflow.operators.python import PythonOperator


# BUG 5: Lambda function names hardcoded -- breaks in staging/prod
EXTRACT_FUNCTION = "fossil-extractor"
TRANSFORM_FUNCTION = "fossil-transformer"


def invoke_lambda_extract():
    client = boto3.client("lambda", region_name="us-east-1")  # BUG 5: region hardcoded
    response = client.invoke(
        FunctionName=EXTRACT_FUNCTION,
        InvocationType="RequestResponse",
        Payload=b"{}",
    )
    # BUG 4: unstructured, not queryable
    print(f"Lambda response status: {response['StatusCode']}")

    # BUG 6: payload error header not inspected -- function crash looks like success
    if response["StatusCode"] != 200:
        raise Exception("Lambda invocation failed")


def invoke_lambda_transform():
    client = boto3.client("lambda", region_name="us-east-1")  # BUG 5: region hardcoded
    response = client.invoke(
        FunctionName=TRANSFORM_FUNCTION,
        InvocationType="RequestResponse",
        Payload=b"{}",
    )
    # BUG 4: unstructured print again
    print("Transform complete")


def notify_complete():
    # BUG 4: no context fields, no structured fields
    print("Pipeline complete!")


# BUG 1 + 2 + 3: no default_args -- zero resilience, zero alerting, zero timeouts
with DAG(
    dag_id="fossil_pipeline",
    start_date=datetime(2024, 1, 1),
    schedule_interval="@daily",
    catchup=False,
    # BUG 2: no alerting callback at DAG level
) as dag:

    extract = PythonOperator(
        task_id="extract_fossils",
        python_callable=invoke_lambda_extract,
        # BUG 1: no resilience -- zero attempts after first failure
        # BUG 3: no time ceiling -- hangs forever on Lambda connection issues
        # BUG 2: no alerting
    )

    transform = PythonOperator(
        task_id="transform_fossils",
        python_callable=invoke_lambda_transform,
        # BUG 1: no resilience
        # BUG 3: no time ceiling
    )

    notify = PythonOperator(
        task_id="notify_complete",
        python_callable=notify_complete,
    )

    extract >> transform >> notify
