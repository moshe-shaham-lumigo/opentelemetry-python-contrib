# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import json
from typing import Any, Dict
from unittest import mock

import botocore.session
from botocore.awsrequest import AWSResponse

from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.test.test_base import TestBase
from opentelemetry.trace import SpanKind
from opentelemetry.trace.span import Span


class TestEventsExtension(TestBase):
    def setUp(self):
        super().setUp()
        BotocoreInstrumentor().instrument()

        session = botocore.session.get_session()
        session.set_credentials(
            access_key="access-key", secret_key="secret-key"
        )
        self.client = session.create_client(
            "events", region_name="us-west-2"
        )
        self.event_bus_name = "my-event-bus"

    def tearDown(self):
        super().tearDown()
        BotocoreInstrumentor().uninstrument()

    @contextlib.contextmanager
    def _mocked_aws_endpoint(self, response):
        response_func = self._make_aws_response_func(response)
        with mock.patch(
            "botocore.endpoint.Endpoint.make_request", new=response_func
        ):
            yield

    @staticmethod
    def _make_aws_response_func(response):
        def _response_func(*args, **kwargs):
            return AWSResponse("http://127.0.0.1", 200, {}, "{}"), response

        return _response_func

    def assert_span(self, name: str) -> Span:
        spans = self.memory_exporter.get_finished_spans()
        self.assertEqual(1, len(spans))
        span = spans[0]

        self.assertEqual(SpanKind.PRODUCER, span.kind)
        self.assertEqual(name, span.name)
        self.assertEqual(
            "aws.events", span.attributes[SpanAttributes.MESSAGING_SYSTEM]
        )

        return span

    def assert_injected_span(self, detail: Dict[str, Any], span: Span):
        # traceparent: <ver>-<trace-id>-<span-id>-<flags>
        trace_parent = detail["traceparent"].split("-")
        span_context = span.get_span_context()

        self.assertEqual(span_context.trace_id, int(trace_parent[1], 16))
        self.assertEqual(span_context.span_id, int(trace_parent[2], 16))

    def test_put_events_injects_span(self):
        mock_response = {
            "FailedEntryCount": 0,
            "Entries": [
                {"EventId": "1"},
                {"EventId": "2"},
            ],
        }

        entries = [
            {
                "Source": "my.source",
                "DetailType": "myDetailType",
                "Detail": json.dumps({"key1": "value1"}),
                "EventBusName": self.event_bus_name,
            },
            {
                "Source": "my.source",
                "DetailType": "myDetailType",
                "Detail": json.dumps({"key2": "value2"}),
                "EventBusName": self.event_bus_name,
            },
        ]

        with self._mocked_aws_endpoint(mock_response):
            self.client.put_events(Entries=entries)

        span = self.assert_span(f"{self.event_bus_name} send")
        self.assertEqual(
            self.event_bus_name,
            span.attributes[SpanAttributes.MESSAGING_DESTINATION_NAME],
        )

        # Verify traceparent was injected into each entry's Detail
        self.assert_injected_span(json.loads(entries[0]["Detail"]), span)
        self.assert_injected_span(json.loads(entries[1]["Detail"]), span)

    def test_put_events_default_event_bus(self):
        mock_response = {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "1"}],
        }

        entries = [
            {
                "Source": "my.source",
                "DetailType": "myDetailType",
                "Detail": json.dumps({"key": "value"}),
            },
        ]

        with self._mocked_aws_endpoint(mock_response):
            self.client.put_events(Entries=entries)

        span = self.assert_span("default send")
        self.assertEqual(
            "default",
            span.attributes[SpanAttributes.MESSAGING_DESTINATION_NAME],
        )
        self.assert_injected_span(json.loads(entries[0]["Detail"]), span)

    def test_put_events_empty_detail(self):
        """Test that injection works when Detail is not provided."""
        mock_response = {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "1"}],
        }

        entries = [
            {
                "Source": "my.source",
                "DetailType": "myDetailType",
                "EventBusName": self.event_bus_name,
            },
        ]

        with self._mocked_aws_endpoint(mock_response):
            self.client.put_events(Entries=entries)

        span = self.assert_span(f"{self.event_bus_name} send")

        # Verify traceparent was injected into the Detail
        injected_detail = json.loads(entries[0]["Detail"])
        self.assert_injected_span(injected_detail, span)
