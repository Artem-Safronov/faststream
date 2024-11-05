from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional, Union
from urllib.parse import urlparse

from faststream._internal._compat import DEF_KEY
from faststream._internal.basic_types import AnyDict, AnyHttpUrl
from faststream._internal.constants import ContentTypes
from faststream.specification.asyncapi.utils import clear_key
from faststream.specification.asyncapi.v2_6_0.generate import move_pydantic_refs
from faststream.specification.asyncapi.v2_6_0.schema import (
    Reference,
    Tag,
    contact_from_spec,
    docs_from_spec,
    license_from_spec,
    tag_from_spec,
)
from faststream.specification.asyncapi.v2_6_0.schema.message import Message
from faststream.specification.asyncapi.v3_0_0.schema import (
    Channel,
    Components,
    Info,
    Operation,
    Schema,
    Server,
    channel_from_spec,
    operation_from_spec,
)
from faststream.specification.asyncapi.v3_0_0.schema.operations import (
    Action,
)

if TYPE_CHECKING:
    from faststream._internal.broker.broker import BrokerUsecase
    from faststream._internal.types import ConnectionType, MsgType
    from faststream.specification.schema.contact import Contact, ContactDict
    from faststream.specification.schema.docs import ExternalDocs, ExternalDocsDict
    from faststream.specification.schema.license import License, LicenseDict
    from faststream.specification.schema.tag import (
        Tag as SpecsTag,
        TagDict as SpecsTagDict,
    )


def get_app_schema(
    broker: "BrokerUsecase[Any, Any]",
    /,
    title: str,
    app_version: str,
    schema_version: str,
    description: str,
    terms_of_service: Optional["AnyHttpUrl"],
    contact: Optional[Union["Contact", "ContactDict", "AnyDict"]],
    license: Optional[Union["License", "LicenseDict", "AnyDict"]],
    identifier: Optional[str],
    tags: Optional[Sequence[Union["SpecsTag", "SpecsTagDict", "AnyDict"]]],
    external_docs: Optional[Union["ExternalDocs", "ExternalDocsDict", "AnyDict"]],
) -> Schema:
    """Get the application schema."""
    broker._setup()

    servers = get_broker_server(broker)
    channels = get_broker_channels(broker)
    operations = get_broker_operations(broker)

    messages: dict[str, Message] = {}
    payloads: dict[str, AnyDict] = {}

    for channel in channels.values():
        channel.servers = [
            {"$ref": f"#/servers/{server_name}"} for server_name in list(servers.keys())
        ]

    for channel_name, channel in channels.items():
        msgs: dict[str, Union[Message, Reference]] = {}
        for message_name, message in channel.messages.items():
            assert isinstance(message, Message)

            msgs[message_name] = _resolve_msg_payloads(
                message_name,
                message,
                channel_name,
                payloads,
                messages,
            )

        channel.messages = msgs

    return Schema(
        info=Info(
            title=title,
            version=app_version,
            description=description,
            termsOfService=terms_of_service,
            contact=contact_from_spec(contact) if contact else None,
            license=license_from_spec(license) if license else None,
            tags=[tag_from_spec(tag) for tag in tags] if tags else None,
            externalDocs=docs_from_spec(external_docs) if external_docs else None,
        ),
        asyncapi=schema_version,
        defaultContentType=ContentTypes.JSON.value,
        id=identifier,
        servers=servers,
        channels=channels,
        operations=operations,
        components=Components(
            messages=messages,
            schemas=payloads,
            securitySchemes=None
            if broker.security is None
            else broker.security.get_schema(),
        ),
    )


def get_broker_server(
    broker: "BrokerUsecase[MsgType, ConnectionType]",
) -> dict[str, Server]:
    """Get the broker server for an application."""
    servers = {}

    tags: Optional[list[Union[Tag, AnyDict]]] = None
    if broker.tags:
        tags = [tag_from_spec(tag) for tag in broker.tags]

    broker_meta: AnyDict = {
        "protocol": broker.protocol,
        "protocolVersion": broker.protocol_version,
        "description": broker.description,
        "tags": tags,
        # TODO
        # "variables": "",
        # "bindings": "",
    }

    if broker.security is not None:
        broker_meta["security"] = broker.security.get_requirement()

    urls = broker.url if isinstance(broker.url, list) else [broker.url]

    for i, broker_url in enumerate(urls, 1):
        server_url = broker_url if "://" in broker_url else f"//{broker_url}"

        parsed_url = urlparse(server_url)
        server_name = "development" if len(urls) == 1 else f"Server{i}"
        servers[server_name] = Server(
            host=parsed_url.netloc,
            pathname=parsed_url.path,
            **broker_meta,
        )

    return servers


def get_broker_operations(
    broker: "BrokerUsecase[MsgType, ConnectionType]",
) -> dict[str, Operation]:
    """Get the broker operations for an application."""
    operations = {}

    for h in broker._subscribers:
        for channel, specs_channel in h.schema().items():
            channel_name = clear_key(channel)

            if specs_channel.subscribe is not None:
                operations[f"{channel_name}Subscribe"] = operation_from_spec(
                    specs_channel.subscribe,
                    Action.RECEIVE,
                    channel_name,
                )

    for p in broker._publishers:
        for channel, specs_channel in p.schema().items():
            channel_name = clear_key(channel)

            if specs_channel.publish is not None:
                operations[f"{channel_name}"] = operation_from_spec(
                    specs_channel.publish,
                    Action.SEND,
                    channel_name,
                )

    return operations


def get_broker_channels(
    broker: "BrokerUsecase[MsgType, ConnectionType]",
) -> dict[str, Channel]:
    """Get the broker channels for an application."""
    channels = {}

    for sub in broker._subscribers:
        channels_schema_v3_0 = {}
        for channel_name, specs_channel in sub.schema().items():
            if specs_channel.subscribe:
                message = specs_channel.subscribe.message
                assert message.title

                *left, right = message.title.split(":")
                message.title = ":".join(left) + f":Subscribe{right}"

                # TODO: why we are format just a key?
                channels_schema_v3_0[clear_key(channel_name)] = channel_from_spec(
                    specs_channel,
                    message,
                    channel_name,
                    "SubscribeMessage",
                )

        channels.update(channels_schema_v3_0)

    for pub in broker._publishers:
        channels_schema_v3_0 = {}
        for channel_name, specs_channel in pub.schema().items():
            if specs_channel.publish:
                channels_schema_v3_0[clear_key(channel_name)] = channel_from_spec(
                    specs_channel,
                    specs_channel.publish.message,
                    channel_name,
                    "Message",
                )

        channels.update(channels_schema_v3_0)

    return channels


def _resolve_msg_payloads(
    message_name: str,
    m: Message,
    channel_name: str,
    payloads: AnyDict,
    messages: AnyDict,
) -> Reference:
    assert isinstance(m.payload, dict)

    m.payload = move_pydantic_refs(m.payload, DEF_KEY)

    message_name = clear_key(message_name)
    channel_name = clear_key(channel_name)

    if DEF_KEY in m.payload:
        payloads.update(m.payload.pop(DEF_KEY))

    one_of = m.payload.get("oneOf", None)
    if isinstance(one_of, dict):
        one_of_list = []
        processed_payloads: dict[str, AnyDict] = {}
        for name, payload in one_of.items():
            processed_payloads[clear_key(name)] = payload
            one_of_list.append(Reference(**{"$ref": f"#/components/schemas/{name}"}))

        payloads.update(processed_payloads)
        m.payload["oneOf"] = one_of_list
        assert m.title
        messages[clear_key(m.title)] = m
        return Reference(
            **{"$ref": f"#/components/messages/{channel_name}:{message_name}"},
        )

    payloads.update(m.payload.pop(DEF_KEY, {}))
    payload_name = m.payload.get("title", f"{channel_name}:{message_name}:Payload")
    payload_name = clear_key(payload_name)
    payloads[payload_name] = m.payload
    m.payload = {"$ref": f"#/components/schemas/{payload_name}"}
    assert m.title
    messages[clear_key(m.title)] = m
    return Reference(
        **{"$ref": f"#/components/messages/{channel_name}:{message_name}"},
    )
