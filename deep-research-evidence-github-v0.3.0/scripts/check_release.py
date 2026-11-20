#!/usr/bin/env python3
"""Read-only release integrity/link check; never invokes a research Agent."""
import hashlib,json,re,sys,zipfile
from pathlib import Path
from urllib.parse import unquote,urlsplit
ROOT=Path(__file__).resolve().parents[1]
def sha(data):return hashlib.sha256(data).hexdigest()
def main():
 errors=[];manifest=json.loads((ROOT/'dist/deep-research-evidence-0.3.0.manifest.json').read_text())
 archive=ROOT/'dist'/manifest['archive']
 if sha(archive.read_bytes())!=manifest['sha256']:errors.append('Archive SHA256 mismatch')
 expected={f['path']:f['sha256'] for f in manifest['files']}
 current={str(p.relative_to(ROOT/'skill')):sha(p.read_bytes()) for p in (ROOT/'skill').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
 if current!=expected:errors.append('Skill files differ from tested v0.3.0 manifest')
 with zipfile.ZipFile(archive) as z:
  if z.testzip() is not None:errors.append('ZIP CRC mismatch')
  got={n.split('/',1)[1]:sha(z.read(n)) for n in z.namelist()}
  if got!=expected:errors.append('Archive contents differ from manifest')
 files=[p for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts and not p.is_relative_to(ROOT/'examples/minimal/run')]
 # Python 3.9 Path.is_relative_to is available.
 checked=0
 for p in files:
  if p.suffix.lower()!='.md':continue
  for target in re.findall(r'\[[^\]\n]+\]\(([^)\n]+)\)',p.read_text(encoding='utf-8')):
   u=urlsplit(target.strip('<>'))
   if u.scheme or u.netloc or not u.path:continue
   checked+=1
   if not (p.parent/unquote(u.path)).exists():errors.append(str(p.relative_to(ROOT))+': broken link '+target)
 report={'version':'0.3.0','skill_files':len(expected),'archive_sha256':manifest['sha256'],'files_excluding_runtime_and_caches':len(files),'total_bytes':sum(p.stat().st_size for p in files),'largest_file_bytes':max(p.stat().st_size for p in files),'local_links_checked':checked,'errors':errors,'research_agent_run':False,'scientific_correctness_certified':False}
 print(json.dumps(report,ensure_ascii=False,indent=2));return 1 if errors else 0
if __name__=='__main__':sys.exit(main())
