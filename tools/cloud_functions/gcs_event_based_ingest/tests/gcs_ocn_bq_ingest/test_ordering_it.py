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
"""integration tests for the ordering behavior of backlog gcs_ocn_bq_ingest"""
import importlib
import multiprocessing
import os
import queue
import time
from typing import List, Optional

import pytest
from google.cloud import bigquery
from google.cloud import storage
from google.cloud.exceptions import NotFound
from tests import utils as test_utils

import gcs_ocn_bq_ingest.common.constants
import gcs_ocn_bq_ingest.common.ordering
import gcs_ocn_bq_ingest.common.utils
import gcs_ocn_bq_ingest.main

TEST_DIR = os.path.realpath(os.path.dirname(__file__) + "/..")


@pytest.mark.IT
@pytest.mark.ORDERING
def test_backlog_publisher(gcs, gcs_bucket, gcs_partitioned_data):
    """Test basic functionality of backlog_publisher
    Drop two success files.
    Assert that both success files are added to backlog and backfill file
    created.
    Assert that that only one backfill file is not recreated.
    """
    test_utils.check_blobs_exist(gcs_partitioned_data,
                                 "test data objects must exist")
    table_prefix = ""
    # load each partition.
    for gcs_data in gcs_partitioned_data:
        if gcs_data.name.endswith(
                gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME):
            table_prefix = gcs_ocn_bq_ingest.common.utils.get_table_prefix(
                gcs, gcs_data)
            gcs_ocn_bq_ingest.common.ordering.backlog_publisher(gcs, gcs_data)

    expected_backlog_blobs = queue.Queue()
    expected_backlog_blobs.put("/".join([
        table_prefix, "_backlog", "$2017041101",
        gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME
    ]))
    expected_backlog_blobs.put("/".join([
        table_prefix, "_backlog", "$2017041102",
        gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME
    ]))

    for backlog_blob in gcs_bucket.list_blobs(
            prefix=f"{table_prefix}/_backlog"):
        assert backlog_blob.name == expected_backlog_blobs.get(block=False)

    backfill_blob: storage.Blob = gcs_bucket.blob(
        f"{table_prefix}/{gcs_ocn_bq_ingest.common.constants.BACKFILL_FILENAME}"
    )
    assert backfill_blob.exists()


@pytest.mark.IT
@pytest.mark.ORDERING
def test_backlog_publisher_with_existing_backfill_file(gcs, gcs_bucket,
                                                       dest_dataset,
                                                       dest_partitioned_table,
                                                       gcs_partitioned_data):
    """Test basic functionality of backlog_publisher when the backfill is
    already running. It should not repost this backfill file.
    """
    test_utils.check_blobs_exist(gcs_partitioned_data,
                                 "test data objects must exist")
    table_prefix = "/".join(
        [dest_dataset.dataset_id, dest_partitioned_table.table_id])
    backfill_blob: storage.Blob = gcs_bucket.blob(
        f"{table_prefix}/{gcs_ocn_bq_ingest.common.constants.BACKFILL_FILENAME}"
    )
    backfill_blob.upload_from_string("")
    backfill_blob.reload()
    original_backfill_blob_generation = backfill_blob.generation
    table_prefix = ""
    # load each partition.
    for gcs_data in gcs_partitioned_data:
        if gcs_data.name.endswith(
                gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME):
            table_prefix = gcs_ocn_bq_ingest.common.utils.get_table_prefix(
                gcs, gcs_data)
            gcs_ocn_bq_ingest.common.ordering.backlog_publisher(gcs, gcs_data)

    # Use of queue to test that list responses are returned in expected order.
    expected_backlog_blobs = queue.Queue()
    expected_backlog_blobs.put("/".join([
        table_prefix, "_backlog", "$2017041101",
        gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME
    ]))
    expected_backlog_blobs.put("/".join([
        table_prefix, "_backlog", "$2017041102",
        gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME
    ]))

    for backlog_blob in gcs_bucket.list_blobs(
            prefix=f"{table_prefix}/_backlog"):
        assert backlog_blob.name == expected_backlog_blobs.get(block=False)

    backfill_blob.reload()
    assert backfill_blob.generation == original_backfill_blob_generation


@pytest.mark.IT
@pytest.mark.ORDERING
def test_backlog_subscriber_in_order_with_new_batch_after_exit(
        bq, gcs, gcs_bucket, dest_dataset, dest_ordered_update_table,
        gcs_ordered_update_data, gcs_external_update_config, gcs_backlog):
    """Test basic functionality of backlog subscriber.
    Populate a backlog with 3 files that make updates where we can assert
    that these jobs were applied in order.

    To ensure that the subscriber cleans up properly after itself before exit,
    we will drop a 4th batch after the subscriber has exited and assert that it
    gets applied as expected.
    """
    test_utils.check_blobs_exist(gcs_external_update_config,
                                 "config objects must exist")
    test_utils.check_blobs_exist(gcs_ordered_update_data,
                                 "test data objects must exist")
    for blob in gcs_external_update_config:
        basename = os.path.basename(blob.name)
        # Only perform the following actions for the backfill config file
        if basename == gcs_ocn_bq_ingest.common.constants.BACKFILL_FILENAME:
            _run_subscriber(gcs, bq, blob)
            table_prefix = gcs_ocn_bq_ingest.common.utils.get_table_prefix(
                gcs, blob)
            backlog_blobs = gcs_bucket.list_blobs(
                prefix=f"{table_prefix}/_backlog/")
            assert backlog_blobs.num_results == 0, "backlog is not empty"
            bqlock_blob: storage.Blob = gcs_bucket.blob("_bqlock")
            assert not bqlock_blob.exists(), "_bqlock was not cleaned up"
            rows = bq.query("SELECT alpha_update FROM "
                            f"{dest_ordered_update_table.dataset_id}"
                            f".{dest_ordered_update_table.table_id}")
            expected_num_rows = 1
            num_rows = 0
            for row in rows:
                num_rows += 1
                assert row[
                    "alpha_update"] == "ABC", "backlog not applied in order"
            assert num_rows == expected_num_rows

            # Now we will test what happens when the publisher posts another
            # batch after the backlog subscriber has exited.
            backfill_blob = _post_a_new_batch(gcs_bucket,
                                              dest_ordered_update_table)
            assert backfill_blob is not None

            _run_subscriber(gcs, bq, backfill_blob)

            rows = bq.query("SELECT alpha_update FROM "
                            f"{dest_ordered_update_table.dataset_id}"
                            f".{dest_ordered_update_table.table_id}")
            expected_num_rows = 1
            num_rows = 0
            for row in rows:
                num_rows += 1
                assert row[
                    "alpha_update"] == "ABCD", "new incremental not applied"
            assert num_rows == expected_num_rows


@pytest.mark.IT
@pytest.mark.ORDERING
def test_backlog_subscriber_in_order_with_new_batch_while_running(
        bq, gcs, gcs_bucket, dest_ordered_update_table: bigquery.Table,
        gcs_ordered_update_data: List[storage.Blob],
        gcs_external_update_config: List[storage.Blob],
        gcs_backlog: List[storage.Blob]):
    """Test functionality of backlog subscriber when new batches are added
    before the subscriber is done finishing the existing backlog.

    Populate a backlog with 3 files that make updates where we can assert
    that these jobs were applied in order.
    In another process populate a fourth batch, and call the publisher.
    """
    test_utils.check_blobs_exist(gcs_external_update_config,
                                 "config objects must exist")
    test_utils.check_blobs_exist(gcs_ordered_update_data,
                                 "test data objects must exist")
    # Cannot pickle clients to another process so we need to recreate some
    # objects without the client property.
    for blob in gcs_external_update_config:
        basename = os.path.basename(blob.name)
        # Only perform the following actions for the backfill config file
        if basename == gcs_ocn_bq_ingest.common.constants.BACKFILL_FILENAME:
            backfill_blob = storage.Blob.from_string(f"gs://{blob.bucket.name}/"
                                                     f"{blob.name}")
            bkt = storage.Bucket(None, gcs_bucket.name)
            claim_blob: storage.Blob = blob.bucket.blob(
                blob.name.replace(
                    basename, f"_claimed_{basename}_created_at_"
                    f"{blob.time_created.timestamp()}"))
            # Run subscriber w/ backlog and publisher w/ new batch in parallel.
            with multiprocessing.Pool(processes=3) as pool:
                res_subscriber = pool.apply_async(_run_subscriber,
                                                  (None, None, backfill_blob))
                # wait for existence of claim blob
                # to ensure subscriber is running.
                while not claim_blob.exists():
                    pass
                res_backlog_publisher = pool.apply_async(
                    _post_a_new_batch, (bkt, dest_ordered_update_table))
                res_backlog_publisher.wait()
                res_monitor = pool.apply_async(
                    gcs_ocn_bq_ingest.common.ordering.subscriber_monitor,
                    (None, bkt,
                     storage.Blob(
                         f"{dest_ordered_update_table.project}"
                         f".{dest_ordered_update_table.dataset_id}/"
                         f"{dest_ordered_update_table.table_id}/"
                         f"_backlog/04/_SUCCESS", bkt)))

                if res_monitor.get():
                    print("subscriber monitor had to retrigger subscriber loop")
                    backfill_blob.reload(client=gcs)
                    _run_subscriber(None, None, backfill_blob)

                res_subscriber.wait()

            table_prefix = gcs_ocn_bq_ingest.common.utils.get_table_prefix(
                gcs, blob)
            backlog_blobs = gcs_bucket.list_blobs(prefix=f"{table_prefix}/"
                                                  f"_backlog/")
            assert backlog_blobs.num_results == 0, "backlog is not empty"
            bqlock_blob: storage.Blob = gcs_bucket.blob("_bqlock")
            assert not bqlock_blob.exists(), "_bqlock was not cleaned up"
            rows = bq.query("SELECT alpha_update FROM "
                            f"{dest_ordered_update_table.dataset_id}"
                            f".{dest_ordered_update_table.table_id}")
            expected_num_rows = 1
            num_rows = 0
            for row in rows:
                num_rows += 1
                assert row[
                    "alpha_update"] == "ABCD", "backlog not applied in order"
            assert num_rows == expected_num_rows


@pytest.mark.IT
@pytest.mark.ORDERING
def test_ordered_load_parquet(monkeypatch, gcs, bq, gcs_bucket,
                              gcs_destination_parquet_config,
                              gcs_external_partitioned_parquet_config,
                              gcs_split_path_batched_parquet_data,
                              dest_partitioned_table):
    """Test ordered loads of parquet data files

    Set global env variable ORDER_PER_TABLE so that all loads are ordered.
    Test to make sure that parquet data files are loaded in order.
    """
    monkeypatch.setenv("ORDER_PER_TABLE", "True")
    monkeypatch.setenv("START_BACKFILL_FILENAME", "_HISTORYDONE")
    # Must reload the constants file in order to pick up testing mock env vars
    importlib.reload(gcs_ocn_bq_ingest.common.constants)

    test_utils.check_blobs_exist(gcs_split_path_batched_parquet_data,
                                 "test data objects must exist")

    table_prefix = ""
    for gcs_data in gcs_split_path_batched_parquet_data:
        if gcs_data.name.endswith(
                gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME):
            table_prefix = gcs_ocn_bq_ingest.common.utils.get_table_prefix(
                gcs, gcs_data)
            break

    # Invoke cloud function for all data blobs and _SUCCESS blob.
    # Cloud function shouldn't take any action at this point because there is
    # no _HISTORYDONE file yet.
    test_utils.trigger_gcf_for_each_blob(gcs_split_path_batched_parquet_data)

    # Upload _HISTORYDONE file which will cause cloud function to take action
    backfill_start_blob: storage.Blob = gcs_bucket.blob(
        f"{table_prefix}/"
        f"{gcs_ocn_bq_ingest.common.constants.START_BACKFILL_FILENAME}")
    backfill_start_blob.upload_from_string("")
    test_utils.check_blobs_exist([backfill_start_blob], "_HISTORYDONE file was"
                                 "not created.")
    test_utils.trigger_gcf_for_each_blob([backfill_start_blob])

    # Check to make sure _BACKFILL file has been craeted
    backfill_blob: storage.Blob = gcs_bucket.blob(
        f"{table_prefix}/{gcs_ocn_bq_ingest.common.constants.BACKFILL_FILENAME}"
    )
    test_utils.check_blobs_exist([backfill_blob],
                                 "_BACKFILL file was not created by method"
                                 "start_backfill_subscriber_if_not_running")
    test_utils.trigger_gcf_for_each_blob([backfill_blob])
    expected_num_rows = 100
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


@pytest.mark.IT
@pytest.mark.ORDERING
def test_ordered_load_parquet_wait_for_validation(
        monkeypatch, gcs, bq, gcs_bucket, gcs_destination_parquet_config,
        gcs_external_partitioned_parquet_config,
        gcs_split_path_batched_parquet_data, dest_partitioned_table):
    """Test ordered loads of parquet data files with a validation step
    between each load.

    Set global env variable ORDER_PER_TABLE so that all loads are ordered.
    Test to make sure that parquet data files are loaded in order.
    """
    monkeypatch.setenv("ORDER_PER_TABLE", "True")
    monkeypatch.setenv("START_BACKFILL_FILENAME", "_HISTORYDONE")
    monkeypatch.setenv("WAIT_FOR_VALIDATION", "True")
    # Must reload the constants file in order to pick up testing mock env vars
    importlib.reload(gcs_ocn_bq_ingest.common.constants)

    test_utils.check_blobs_exist(gcs_split_path_batched_parquet_data,
                                 "test data objects must exist")

    table_prefix = ""
    for gcs_data in gcs_split_path_batched_parquet_data:
        if gcs_data.name.endswith(
                gcs_ocn_bq_ingest.common.constants.SUCCESS_FILENAME):
            table_prefix = gcs_ocn_bq_ingest.common.utils.get_table_prefix(
                gcs, gcs_data)
            break

    # Upload _HISTORYDONE file which will cause cloud function to take action
    backfill_start_blob: storage.Blob = gcs_bucket.blob(
        f"{table_prefix}/"
        f"{gcs_ocn_bq_ingest.common.constants.START_BACKFILL_FILENAME}")
    backfill_start_blob.upload_from_string("")
    test_utils.check_blobs_exist([backfill_start_blob], "_HISTORYDONE file was"
                                 "not created.")
    test_utils.trigger_gcf_for_each_blob([backfill_start_blob])

    # Invoke cloud function for all data blobs and _SUCCESS blob.
    # Cloud function shouldn't take any action at this point because there is
    # no _HISTORYDONE file yet.
    test_utils.trigger_gcf_for_each_blob(gcs_split_path_batched_parquet_data)

    # Check to make sure _BACKFILL file has been craeted
    backfill_blob: storage.Blob = gcs_bucket.blob(
        f"{table_prefix}/{gcs_ocn_bq_ingest.common.constants.BACKFILL_FILENAME}"
    )
    test_utils.check_blobs_exist([backfill_blob],
                                 "_BACKFILL file was not created by method"
                                 "start_backfill_subscriber_if_not_running")
    test_utils.trigger_gcf_for_each_blob([backfill_blob])

    # Test to make sure that _bqlock is not present since cloud function should
    # remove the lock in between validations
    with pytest.raises(NotFound):
        test_utils.check_blobs_exist(
            [gcs_bucket.blob(f"{table_prefix}/_bqlock")])

    # Check that the first batch of data was loaded but only the first batch,
    # since the second batch is waiting on confirmation of validation.
    expected_num_rows = 50
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)

    # Upload _BACKFILL file to signal that validation has completed and
    # that the next item in the _backlog can be processed.
    backfill_blob.upload_from_string("")
    test_utils.trigger_gcf_for_each_blob([backfill_blob])

    # Check that the second batch was loaded
    expected_num_rows = 100
    test_utils.bq_wait_for_rows(bq, dest_partitioned_table, expected_num_rows)


def _run_subscriber(gcs_client: Optional[storage.Client],
                    bq_client: Optional[bigquery.Client], backfill_blob):
    gcs_ocn_bq_ingest.common.ordering.backlog_subscriber(
        gcs_client, bq_client, backfill_blob, time.monotonic())


def _post_a_new_batch(gcs_bucket, dest_ordered_update_table):
    # We may run this in another process and cannot pickle client objects
    gcs = storage.Client()
    data_obj: storage.Blob
    for test_file in ["data.csv", "_SUCCESS"]:
        data_obj = gcs_bucket.blob("/".join([
            f"{dest_ordered_update_table.project}."
            f"{dest_ordered_update_table.dataset_id}",
            dest_ordered_update_table.table_id, "04", test_file
        ]))
        data_obj.upload_from_filename(os.path.join(TEST_DIR, "resources",
                                                   "test-data", "ordering",
                                                   "04", test_file),
                                      client=gcs)
    return gcs_ocn_bq_ingest.common.ordering.backlog_publisher(gcs, data_obj)
