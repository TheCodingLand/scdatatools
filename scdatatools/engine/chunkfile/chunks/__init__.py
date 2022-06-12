import typing

from .base import ChunkHeader, Chunk
from .data import *
from .defs import (
    ChunkType,
    CHUNK_CLASSES,
    CHUNK_HEADER_CLASSES,
    CHUNK_FILE_HEADER_CLASSES,
)
from .geometry import *
from .soc import *
from .unknown import *

logger = logging.getLogger(__name__)


def chunk_file_header_for_signature(chunk_file_signature) -> typing.Type[ChunkHeader]:
    """Returns the appropriate `ChunkFileHeader` class for the given `chunk_file_signature`"""
    return CHUNK_FILE_HEADER_CLASSES[bytes(chunk_file_signature)]


def header_class_for_version(chunk_file_version) -> typing.Type[ChunkHeader]:
    """Returns the appropriate `ChunkHeader` class for the given `chunk_file_version`"""
    return CHUNK_HEADER_CLASSES[chunk_file_version]


def chunk_class_from_header(chunk_header: ChunkHeader, fallback=Chunk) -> typing.Type[Chunk]:
    """Returns the appropriate class for the given `chunk_header`"""
    chunk_versions = CHUNK_CLASSES.setdefault(chunk_header.type, {})
    if chunk_header.version not in chunk_versions:
        logger.debug(f"Unhandled chunk {chunk_header}")
        return fallback
    return chunk_versions[chunk_header.version]


def chunk_from_header(hdr: ChunkHeader, data: (bytearray, bytes), chunk_file, fallback_class=Chunk):
    """
    :param hdr: `ChunkHeader` describing the Chunk in `data`
    :param data: Data to read chunk from
    :param chunk_file: The `Model` this chunk belongs to
    :param fallback_class: The `Chunk` base class to be used as a fallback if a specific class cannot be determined
    :return: `Chunk`
    """
    return chunk_class_from_header(hdr, fallback=fallback_class).from_buffer(hdr, data, chunk_file)
