from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.char.CustomCharLoader import inspect_team_archive


project = Path(__file__).resolve().parent.parent
folder = project / "teams/Qingxiao_Sanhua_Verina"
workshop = project / "configs/custom_teams/Qingxiao__Sanhua__Verina"
members = ["team.json", "Qingxiao.py", "Sanhua.py", "Verina.py"]
for name in members[1:]:
    source = (folder / name).read_bytes()
    if (workshop / name).read_bytes() != source:
        raise RuntimeError(f"Workshop source differs: {name}")
    if (workshop / name.replace(".py", ".import.py")).read_bytes() != source:
        raise RuntimeError(f"Workshop import differs: {name}")
archive_path = project / "Qingxiao_Sanhua_Verina.zip"
with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name in members:
        archive.write(folder / name, name)
inspect_team_archive(archive_path)
with zipfile.ZipFile(archive_path) as archive:
    if sorted(archive.namelist()) != sorted(members):
        raise RuntimeError("Unexpected archive members")
    for name in members:
        if archive.read(name) != (folder / name).read_bytes():
            raise RuntimeError(f"Archive source differs: {name}")
print("Workshop source and exported archive verified")
