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

import abc
import inspect
import json
import logging
from typing import Any, Dict, MutableMapping

from opentelemetry.propagate import inject
from opentelemetry.instrumentation.botocore.extensions.types import (
    _AttributeMapT,
    _AwsSdkCallContext,
    _AwsSdkExtension,
    _BotocoreInstrumentorContext,
)
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.trace import SpanKind
from opentelemetry.trace.span import Span

_logger = logging.getLogger(__name__)

################################################################################
# EventBridge operations
################################################################################


class _EventsOperation(abc.ABC):
    @classmethod
    @abc.abstractmethod
    def operation_name(cls) -> str:
        pass

    @classmethod
    def span_kind(cls) -> SpanKind:
        return SpanKind.CLIENT

    @classmethod
    def extract_attributes(
        cls, call_context: _AwsSdkCallContext, attributes: _AttributeMapT
    ):
        pass

    @classmethod
    def before_service_call(cls, call_context: _AwsSdkCallContext, span: Span):
        pass


class _OpPutEvents(_EventsOperation):
    @classmethod
    def operation_name(cls) -> str:
        return "PutEvents"

    @classmethod
    def span_kind(cls) -> SpanKind:
        return SpanKind.PRODUCER

    @classmethod
    def extract_attributes(
        cls, call_context: _AwsSdkCallContext, attributes: _AttributeMapT
    ):
        entries = call_context.params.get("Entries", [])
        if entries:
            # Use the first entry's EventBusName as destination
            event_bus_name = entries[0].get("EventBusName", "default")
            call_context.span_name = f"{event_bus_name} send"
            attributes[SpanAttributes.MESSAGING_DESTINATION_NAME] = (
                event_bus_name
            )

    @classmethod
    def before_service_call(cls, call_context: _AwsSdkCallContext, span: Span):
        for entry in call_context.params.get("Entries", []):
            cls._inject_span_into_entry(entry)

    @classmethod
    def _inject_span_into_entry(cls, entry: MutableMapping[str, Any]):
        """Inject trace context into the Detail JSON payload."""
        detail_str = entry.get("Detail")
        if detail_str is None:
            detail = {}
        else:
            try:
                detail = json.loads(detail_str)
            except (json.JSONDecodeError, TypeError):
                _logger.debug(
                    "botocore instrumentation: failed to parse EventBridge Detail as JSON"
                )
                return

        # Inject trace context directly into the detail dict
        inject(detail)
        entry["Detail"] = json.dumps(detail)


################################################################################
# EventBridge extension
################################################################################

_OPERATION_MAPPING: Dict[str, _EventsOperation] = {
    op.operation_name(): op
    for op in globals().values()
    if inspect.isclass(op)
    and issubclass(op, _EventsOperation)
    and not inspect.isabstract(op)
}


class _EventsExtension(_AwsSdkExtension):
    def __init__(self, call_context: _AwsSdkCallContext):
        super().__init__(call_context)
        self._op = _OPERATION_MAPPING.get(call_context.operation)
        if self._op:
            call_context.span_kind = self._op.span_kind()

    def extract_attributes(self, attributes: _AttributeMapT):
        attributes[SpanAttributes.MESSAGING_SYSTEM] = "aws.events"

        if self._op:
            self._op.extract_attributes(self._call_context, attributes)

    def before_service_call(
        self, span: Span, instrumentor_context: _BotocoreInstrumentorContext
    ):
        if self._op:
            self._op.before_service_call(self._call_context, span)
