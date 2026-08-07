"""MQTT map download commands (P2P stream trigger via DPS)."""

from __future__ import annotations

from ..const import NOVEL_DPS_MAP
from ..proto.cloud.multi_maps_pb2 import MultiMapsManageRequest
from .proto_utils import encode_protobuf_message

MULTI_MAP_DPS = NOVEL_DPS_MAP.get("MULTI_MAP_MANAGE", "172")


def build_map_get_all_command(seq: int = 1) -> dict[str, str]:
    """Request all maps; device streams pixels on biz/ protocol-41."""
    req = MultiMapsManageRequest(
        method=MultiMapsManageRequest.MAP_GET_ALL,
        seq=seq,
    )
    return {MULTI_MAP_DPS: encode_protobuf_message(req)}


def build_map_get_one_command(cloud_mapid: int, seq: int = 1) -> dict[str, str]:
    """Request a single saved map by cloud map id."""
    req = MultiMapsManageRequest(
        method=MultiMapsManageRequest.MAP_GET_ONE,
        seq=seq,
        common=MultiMapsManageRequest.Common(cloud_mapid=int(cloud_mapid)),
    )
    return {MULTI_MAP_DPS: encode_protobuf_message(req)}
