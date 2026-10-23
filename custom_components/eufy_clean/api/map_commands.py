"""MQTT map download commands (P2P stream trigger via DPS)."""

from __future__ import annotations

from ..const import NOVEL_DPS_MAP
from ..proto.cloud.multi_maps_pb2 import MultiMapsManageRequest
from .proto_utils import encode_protobuf_message, encode_restricted_zones_command

MULTI_MAP_DPS = NOVEL_DPS_MAP.get("MULTI_MAP_MANAGE", "172")
# The app writes MapEditRequest to DPS 170 (ecl_map_edit_*).
MAP_EDIT_DPS = "170"


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


def build_set_zones_command(
    virtual_walls: list[tuple[tuple[int, int], tuple[int, int]]],
    forbidden: list[list[tuple[int, int]]],
    ban_mop: list[list[tuple[int, int]]],
    map_id: int = 0,
    releases: int = 0,
) -> dict[str, str]:
    """Replace no-go and no-mop zones, keeping the current virtual walls."""
    return {
        MAP_EDIT_DPS: encode_restricted_zones_command(
            virtual_walls, forbidden, ban_mop, map_id, releases
        )
    }
