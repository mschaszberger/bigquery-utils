# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Common test utilities"""
from typing import List

import google.cloud.exceptions
from google.cloud import storage

import gcs_ocn_bq_ingest.main


def trigger_gcf_for_each_blob(blobs: List[storage.blob.Blob]):
    for blob in blobs:
        test_event = {
            "attributes": {
                "bucketId": blob.bucket.name,
                "objectId": blob.name
            }
        }
        gcs_ocn_bq_ingest.main.main(test_event, None)


def check_blobs_exist(blobs: List[storage.blob.Blob], error_msg_if_missing):
    if not all(blob.exists() for blob in blobs):
        raise google.cloud.exceptions.NotFound(error_msg_if_missing)
