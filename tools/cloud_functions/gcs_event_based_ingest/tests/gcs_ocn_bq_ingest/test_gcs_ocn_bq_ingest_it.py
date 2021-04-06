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
"""integration tests for gcs_ocn_bq_ingest"""
import os
from typing import List

import google.cloud.exceptions
import pytest
from google.cloud import bigquery
from google.cloud import storage

import gcs_ocn_bq_ingest.main
import gcs_ocn_bq_ingest.common.utils
from tests import utils as test_utils

TEST_DIR = os.path.realpath(os.path.dirname(__file__) + "/..")
LOAD_JOB_POLLING_TIMEOUT = 20  # seconds


@pytest.mark.IT
def test_load_job(bq, gcs_data, dest_dataset, dest_table, mock_env):
    """tests basic single invocation with load job"""
    test_utils.check_blobs_exist(gcs_data, "test data objects must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_data)
    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    expected_num_rows = sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_table, expected_num_rows)


@pytest.mark.IT
def test_gcf_event_schema(bq, gcs_data, dest_dataset, dest_table, mock_env):
    """tests compatibility to Cloud Functions Background Function posting the
    storage object schema
    https://cloud.google.com/storage/docs/json_api/v1/objects#resource
    directly based on object finalize.

    https://cloud.google.com/functions/docs/tutorials/storage#functions_tutorial_helloworld_storage-python
    """
    test_utils.check_blobs_exist(gcs_data, "test data objects must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_data)
    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    expected_num_rows = sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_table, expected_num_rows)


@pytest.mark.IT
def test_duplicate_success_notification(bq, gcs_data, dest_dataset, dest_table,
                                        mock_env):
    """tests behavior with two notifications for the same success file."""
    test_utils.check_blobs_exist(gcs_data, "test data objects must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_data)
    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    expected_num_rows = sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_table, expected_num_rows)


@pytest.mark.IT
def test_load_job_truncating_batches(bq, gcs_batched_data,
                                     gcs_truncating_load_config, dest_dataset,
                                     dest_table, mock_env):
    """
    tests two successive batches with a load.json that dictates WRITE_TRUNCATE.

    after both load jobs the count should be the same as the number of lines
    in the test file because we should pick up the WRITE_TRUNCATE disposition.
    """
    test_utils.check_blobs_exist(
        gcs_truncating_load_config,
        "the test is not configured correctly the load.json is missing")
    test_utils.check_blobs_exist(gcs_batched_data,
                                 "test data objects must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_batched_data)

    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    expected_num_rows = sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_table, expected_num_rows)


@pytest.mark.IT
def test_load_job_appending_batches(bq, gcs_batched_data, dest_dataset,
                                    dest_table, mock_env):
    """
    tests two loading batches with the default load configuration.

    The total number of rows expected should be the number of rows
    in the test file multiplied by the number of batches because we
    should pick up the default WRITE_APPEND disposition.
    """
    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    test_count = sum(1 for _ in open(test_data_file))
    expected_counts = 2 * test_count  # 2 batches * num of test rows
    test_utils.check_blobs_exist(gcs_batched_data,
                                 "test data objects must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_batched_data)
    test_utils.bq_wait_for_rows(bq, dest_table, expected_counts)


@pytest.mark.IT
def test_external_query_pure(bq, gcs_data, gcs_external_config, dest_dataset,
                             dest_table, mock_env):
    """tests the basic external query ingrestion mechanics
    with bq_transform.sql and external.json
    """
    test_utils.check_blobs_exist(gcs_data, "test data objects must exist")
    test_utils.check_blobs_exist(gcs_external_config,
                                 "config objects must exist")

    test_utils.trigger_gcf_for_each_blob(gcs_data)
    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    expected_num_rows = sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_table, expected_num_rows)


@pytest.mark.IT
def test_load_job_partitioned(bq, gcs_partitioned_data,
                              gcs_truncating_load_config, dest_dataset,
                              dest_partitioned_table, mock_env):
    """
    Test loading separate partitions with WRITE_TRUNCATE

    after both load jobs the count should equal the sum of the test data in both
    partitions despite having WRITE_TRUNCATE disposition because the destination
    table should target only a particular partition with a decorator.
    """
    test_utils.check_blobs_exist(gcs_truncating_load_config,
                                 "the load.json is missing")
    test_utils.check_blobs_exist(gcs_partitioned_data,
                                 "test data objects must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_partitioned_data)
    expected_num_rows = 0
    for part in ["$2017041101", "$2017041102"]:
        test_data_file = os.path.join(TEST_DIR, "resources", "test-data",
                                      "nyc_311", part, "nyc_311.csv")
        expected_num_rows += sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


@pytest.mark.IT
def test_external_query_partitioned(bq, gcs_partitioned_data,
                                    gcs_external_partitioned_config,
                                    dest_dataset, dest_partitioned_table,
                                    mock_env):
    """tests the basic external query ingrestion mechanics
    with bq_transform.sql and external.json
    """
    if not all((blob.exists() for blob in gcs_external_partitioned_config)):
        raise google.cloud.exceptions.NotFound("config objects must exist")

    for blob in gcs_partitioned_data:
        if not blob.exists():
            raise google.cloud.exceptions.NotFound(
                "test data objects must exist")
        test_event = {
            "attributes": {
                "bucketId": blob.bucket.name,
                "objectId": blob.name
            }
        }
        gcs_ocn_bq_ingest.main.main(test_event, None)
    expected_num_rows = 0
    for part in [
            "$2017041101",
            "$2017041102",
    ]:
        test_data_file = os.path.join(TEST_DIR, "resources", "test-data",
                                      "nyc_311", part, "nyc_311.csv")
        expected_num_rows += sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


@pytest.mark.IT
def test_external_query_partitioned_parquet(
        bq, gcs_split_path_partitioned_parquet_data,
        gcs_external_partitioned_parquet_config, gcs_destination_config,
        dest_dataset, dest_partitioned_table, mock_env):
    """tests the basic external query ingrestion mechanics
    with bq_transform.sql and external.json
    """
    test_utils.check_blobs_exist(
        gcs_destination_config + gcs_external_partitioned_parquet_config,
        "config objects must exist")
    test_utils.check_blobs_exist(gcs_split_path_partitioned_parquet_data,
                                 "test data objects must exist")

    test_utils.trigger_gcf_for_each_blob(
        gcs_split_path_partitioned_parquet_data)
    expected_num_rows = 100
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


@pytest.mark.IT
def test_external_query_partitioned_with_destination_config(
        bq, gcs_split_path_partitioned_data, gcs_external_partitioned_config,
        gcs_destination_config, dest_partitioned_table, mock_env):
    """tests the basic external query ingrestion mechanics
    with bq_transform.sql and external.json
    """
    test_utils.check_blobs_exist(
        (gcs_external_partitioned_config + gcs_destination_config),
        "config objects must exist")
    test_utils.check_blobs_exist(gcs_split_path_partitioned_data,
                                 "test data must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_split_path_partitioned_data +
                                         gcs_external_partitioned_config +
                                         gcs_destination_config)
    expected_num_rows = 0
    for part in [
            "$2017041101",
            "$2017041102",
    ]:
        test_data_file = os.path.join(TEST_DIR, "resources", "test-data",
                                      "nyc_311", part, "nyc_311.csv")
        expected_num_rows += sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


@pytest.mark.IT
def test_look_for_config_in_parents(bq, gcs_data_under_sub_dirs,
                                    gcs_external_config, dest_dataset,
                                    dest_table, mock_env):
    """test discovery of configuration files for external query in parent
    _config paths.
    """
    test_utils.check_blobs_exist(gcs_external_config,
                                 "config objects must exist")
    test_utils.check_blobs_exist(gcs_data_under_sub_dirs,
                                 "test data must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_data_under_sub_dirs)
    test_data_file = os.path.join(TEST_DIR, "resources", "test-data", "nation",
                                  "part-m-00001")
    expected_num_rows = sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_table, expected_num_rows)


@pytest.mark.IT
def test_look_for_destination_config_in_parents(
        bq, gcs_split_path_partitioned_data, gcs_destination_config,
        dest_dataset, dest_partitioned_table, mock_env):
    """test discovery of configuration files for destination in parent
    _config paths.
    """
    test_utils.check_blobs_exist(gcs_destination_config,
                                 "config objects must exist")
    test_utils.check_blobs_exist(gcs_split_path_partitioned_data,
                                 "test data must exist")
    test_utils.trigger_gcf_for_each_blob(gcs_split_path_partitioned_data)
    expected_num_rows = 0
    for part in ["$2017041101", "$2017041102"]:
        test_data_file = os.path.join(TEST_DIR, "resources", "test-data",
                                      "nyc_311", part, "nyc_311.csv")
        expected_num_rows += sum(1 for _ in open(test_data_file))
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


@pytest.mark.IT
def test_external_query_with_bad_statement(bq, gcs_data,
                                           gcs_external_config_bad_statement,
                                           dest_dataset, dest_table, mock_env):
    """tests the basic external query ingrestion mechanics
    with bq_transform.sql and external.json
    """
    test_utils.check_blobs_exist(gcs_external_config_bad_statement,
                                 "config objects must exist")
    test_utils.check_blobs_exist(gcs_data, "test data objects must exist")

    with pytest.raises(gcs_ocn_bq_ingest.common.exceptions.BigQueryJobFailure
                      ) as exception_info:
        test_utils.trigger_gcf_for_each_blob(gcs_data)


@pytest.mark.IT
def test_get_batches_for_prefix_recursive(gcs, gcs_partitioned_data,
                                          gcs_external_partitioned_config,
                                          dest_dataset, mock_env):
    """tests that all blobs are recursively found for a given prefix
    """
    if not all((blob.exists() for blob in gcs_external_partitioned_config)):
        raise google.cloud.exceptions.NotFound("config objects must exist")
    blob = gcs_partitioned_data[0]
    gcs_ocn_bq_ingest.common.utils.get_batches_for_gsurl(
        gcs,
        f"gs://{blob.bucket.name}/{dest_dataset.dataset_id}",
        recursive=True)
