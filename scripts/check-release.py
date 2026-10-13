"""Fail if the source release would include private data or large binary assets."""
from pathlib import Path
import json,re,subprocess

ROOT=Path(__file__).resolve().parents[1]
try:
    names=subprocess.check_output(['git','-C',str(ROOT),'ls-files'],text=True,encoding='utf-8',stderr=subprocess.DEVNULL).splitlines() if (ROOT/'.git').exists() else []
except (FileNotFoundError,subprocess.CalledProcessError):
    names=[]
if not names:
    names=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and not any(part in {'.git','data','.cache','node_modules','python','cuda-runtime','__pycache__','.venv','.export-env'} for part in p.relative_to(ROOT).parts) and p.suffix not in {'.pyc','.onnx','.ckpt','.zip','.tgz'}]
errors=[];size=0
for name in names:
    p=ROOT/name
    if not p.is_file():continue
    size+=p.stat().st_size
    if p.stat().st_size>5*1024**2 or p.suffix.lower() in {'.onnx','.ckpt','.dll','.exe','.whl','.wav','.mp3','.mp4','.flac'}:errors.append(f'Binary/model/private audio in release: {name}')
    if any(part in {'data','runtime','.cache','node_modules','cuda-runtime'} for part in Path(name).parts):errors.append(f'Mutable/private runtime path in release: {name}')
    if p.suffix.lower() in {'.js','.py','.ps1','.md','.json','.yml','.yaml','.txt'}:
        content=p.read_text(encoding='utf-8')
        if re.search(r'[A-Z]:[/\\]Users[/\\][^/\\\s\"\']+[/\\]',content):errors.append(f'Personal absolute path in {name}')
        if re.search(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',content):errors.append(f'Credential pattern in {name}')
for file in ['README.md','LICENSE','THIRD_PARTY_NOTICES.md','src/index.js','src/index.d.ts','skills/vocal-music-core/SKILL.md','llms.txt','scripts/install-runtime.ps1','scripts/export-model.py','scripts/diagnose.py']:
    if not (ROOT/file).is_file():errors.append(f'Missing release entrypoint: {file}')
manifest=json.loads((ROOT/'plugins/music/roformer-port/artifacts-balanced/manifest.json').read_text())
assert manifest['models']['mel_band_roformer_kim']['chunk_size']==105840
report={'passed':not errors,'files':len(names),'sourceKiB':round(size/1024,1),'errors':errors}
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if not errors else 1)
