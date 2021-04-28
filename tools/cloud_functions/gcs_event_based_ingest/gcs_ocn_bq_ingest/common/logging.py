import json
from typing import Optional, Union

from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery
from google.cloud.exceptions import ClientError


def log_bigquery_job(job: Union[bigquery.LoadJob, bigquery.QueryJob],
                     table: bigquery.TableReference,
                     message: Optional[str] = None,
                     severity: Optional[str] = 'NOTICE'):
    if job.errors:
        severity = "ERROR"
        message = message or "BigQuery Job had errors."
    elif severity == "ERROR":
        message = message or ("BigQuery Job completed"
                              " but is considered an error.")
    else:
        severity = "NOTICE"
        message = message or "BigQuery Job completed without errors."

    print(
        json.dumps(
            dict(
                message=message,
                severity=severity,
                job=job.to_api_repr(),
                table=table.to_api_repr(),
                errors=job.errors,
            )))


def log_with_table(
    table: bigquery.TableReference,
    message: str,
    severity: Optional[str] = 'NOTICE',
):
    print(
        json.dumps(
            dict(
                message=message,
                severity=severity,
                table=table.to_api_repr(),
            )))


def log_api_error(table: bigquery.TableReference, message: str,
                  error: Union[GoogleAPIError, ClientError]):
    print(
        json.dumps(
            dict(message=message or error.message,
                 severity='ERROR',
                 table=table.to_api_repr(),
                 errors=error.errors or error.message or message)))
