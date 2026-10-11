import argparse
import json
from pathlib import Path

from minidump.constants import MINIDUMP_STREAM_TYPE
from minidump.directory import MINIDUMP_DIRECTORY
from minidump.header import MinidumpHeader
from minidump.streams.ExceptionStream import ExceptionList
from minidump.streams.ModuleListStream import MinidumpModuleList
from minidump.streams.ThreadInfoListStream import MinidumpThreadInfoList


def inspect_metadata(path):
    # 仅解析异常、模块及线程描述，不读取进程内存或线程寄存器。
    with path.open("rb") as source:
        header = MinidumpHeader.parse(source)
        source.seek(header.StreamDirectoryRva)
        directories = [
            MINIDUMP_DIRECTORY.parse(source)
            for _ in range(header.NumberOfStreams)
        ]
        streams = {
            item.StreamType: item for item in directories if item is not None
        }
        modules = MinidumpModuleList.parse(
            streams[MINIDUMP_STREAM_TYPE.ModuleListStream], source
        ).modules
        exceptions = ExceptionList.parse(
            streams[MINIDUMP_STREAM_TYPE.ExceptionStream], source
        ).exception_records
        info_directory = streams.get(MINIDUMP_STREAM_TYPE.ThreadInfoListStream)
        infos = (
            MinidumpThreadInfoList.parse(info_directory, source).infos
            if info_directory is not None else []
        )

    def module_at(address):
        matches = [item for item in modules if item.inrange(address)]
        return [
            {"name": item.name, "relative_address": hex(address - item.baseaddress)}
            for item in matches
        ]

    return {
        "file": str(path),
        "streams": [item.StreamType.name for item in directories if item is not None],
        "module_count": len(modules),
        "exceptions": [
            {
                "thread_id": item.ThreadId,
                "code": hex(item.ExceptionRecord.ExceptionCode_raw),
                "flags": hex(item.ExceptionRecord.ExceptionFlags),
                "address": hex(item.ExceptionRecord.ExceptionAddress),
                "address_modules": module_at(item.ExceptionRecord.ExceptionAddress),
                "parameters": [hex(value) for value in item.ExceptionRecord.ExceptionInformation],
                "parameter_modules": [
                    module_at(value) for value in item.ExceptionRecord.ExceptionInformation
                ],
            }
            for item in exceptions
        ],
        "thread_info": [
            {
                "thread_id": item.ThreadId,
                "create_time": item.CreateTime,
                "start_address": hex(item.StartAddress),
                "start_modules": module_at(item.StartAddress),
                "exit_status": hex(item.ExitStatus),
            }
            for item in infos
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    arguments = parser.parse_args()
    print(json.dumps([inspect_metadata(path) for path in arguments.paths], indent=2, ensure_ascii=False))
