"""Read Windows launcher icon resources as data, without running the launcher."""

import struct
from pathlib import Path

import pefile


def launcher_icon(project: Path) -> bytes | None:
    for path in sorted(project.glob("*.exe"))[:4]:
        try:
            if path.is_symlink() or path.stat().st_size > 16 * 1024**2:
                continue
            with pefile.PE(data=path.read_bytes(), fast_load=True) as pe:
                pe.parse_data_directories(
                    directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
                )
                resources = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
                if resources is None:
                    continue
                groups, images = [], {}
                for entry in resources.entries[:512]:
                    if entry.id not in (3, 14) or not hasattr(entry, "directory"):
                        continue
                    for resource in entry.directory.entries[:256]:
                        if not hasattr(resource, "directory") or not resource.directory.entries:
                            continue
                        leaf = resource.directory.entries[0]
                        if not hasattr(leaf, "data"):
                            continue
                        data = leaf.data.struct
                        if data.Size > 4 * 1024**2:
                            continue
                        payload = pe.get_data(data.OffsetToData, data.Size)
                        if len(payload) != data.Size:
                            continue
                        if entry.id == 3:
                            images[resource.id] = payload
                        else:
                            groups.append(payload)
                for group in groups:
                    icon = assemble_icon(group, images)
                    if icon:
                        return icon
        except (OSError, pefile.PEFormatError, ValueError, struct.error, OverflowError):
            continue
    return None


def assemble_icon(group: bytes, images: dict[int, bytes]) -> bytes | None:
    if len(group) < 6:
        return None
    reserved, kind, count = struct.unpack_from("<HHH", group)
    if reserved or kind != 1 or not 0 < count <= 256 or len(group) != 6 + 14 * count:
        return None
    directory, payload = bytearray(group[:6]), bytearray()
    offset = 6 + 16 * count
    for index in range(count):
        entry = group[6 + 14 * index : 20 + 14 * index]
        size, resource_id = struct.unpack_from("<IH", entry, 8)
        image = images.get(resource_id)
        if not image or len(image) != size or offset + len(image) > 4 * 1024**2:
            return None
        directory.extend(entry[:12] + struct.pack("<I", offset))
        payload.extend(image)
        offset += len(image)
    return bytes(directory + payload)
