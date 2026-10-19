import pathlib,subprocess,re,shutil,json,os,sys
root=pathlib.Path(sys.argv[1]).resolve()
prefix=root/'ports'; dest=root/'Nexxus-Mini-AI'; dest.mkdir(exist_ok=True)
transport=dest/'transport';(transport/'lib').mkdir(parents=True,exist_ok=True)
project=pathlib.Path(__file__).resolve().parents[2]
for name in ['client.py','diagnose.py','Comprobar.command','Nexxus.command','INSTALACION.md','config.example.json','system-metrics.c','nebula.json']:
 p=project/'clients/nexxus-mini'/name
 shutil.copy2(p,dest/p.name)
shutil.copy2(root/'system-metrics-intel',dest/'system-metrics')
for obsolete in ['record-audio','record-audio.c']:
 if (dest/obsolete).exists():(dest/obsolete).unlink()
files={};report=[]
def output(*cmd):return subprocess.check_output(cmd,text=True)
def deps(path):return [line.strip().split(' (compatibility')[0] for line in output('otool','-L',str(path)).splitlines()[1:] if line.strip()]
def include(original,executable=False):
 if original in files:return
 src=(prefix/original.lstrip('/')).resolve()
 if not src.is_relative_to(prefix.resolve()):raise ValueError('Outside prefix')
 target=transport/('curl' if executable else 'lib/'+pathlib.Path(original).name)
 if target.exists():target.chmod(0o755)
 shutil.copyfile(src,target);target.chmod(0o755);files[original]=target
 load=deps(src)
 for dep in load:
  if dep==original:continue
  if dep.startswith('/opt/local/'):include(dep)
  elif not dep.startswith(('/usr/lib/','/System/Library/')):raise ValueError('Unknown dependency '+dep)
 args=['install_name_tool']
 if not executable:args+=['-id','@loader_path/'+target.name]
 for dep in load:
  if dep.startswith('/opt/local/') and dep!=original:
   replacement=('@executable_path/lib/' if executable else '@loader_path/')+pathlib.Path(dep).name
   args+=['-change',dep,replacement]
 if len(args)>1:subprocess.run(args+[str(target)],check=True)
 commands=output('otool','-l',str(target))
 versions=re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)',commands)
 versions += re.findall(r'cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform \d+\s+minos ([\d.]+)',commands)
 if any(tuple(map(int,v.split('.')))>(10,6) for v in versions):raise ValueError(('Minimum OS too recent',original,versions))
 if '/opt/local/' in '\n'.join(deps(target)):raise ValueError('Unrelocated '+original)
 report.append({'file':str(target.relative_to(dest)),'minimum_macos':versions,'dependencies':deps(target)})
include('/opt/local/bin/curl',True)
# Match all non-system imports against the signed libraries, including substituted ABI-compatible versions.
exports={}
for original,target in files.items():
 if original.endswith('.dylib'):
  name=re.sub(r'(?:\.\d+)*\.dylib$','',pathlib.Path(original).name)
  symbols={line.split()[-1] for line in output('nm','-gU',str(target)).splitlines() if line.split()}
  exports[name]=symbols
for original,target in files.items():
 for line in output('nm','-m','-u',str(target)).splitlines():
  m=re.search(r'external (\S+) \(from ([^)]+)\)',line)
  if m and m[2] in exports and m[1] not in exports[m[2]]:raise ValueError(('Missing imported symbol',original,m[1],m[2]))
ca=prefix/'opt/local/share/curl/curl-ca-bundle.crt'
if not ca.exists():raise ValueError('CA bundle not found')
shutil.copyfile(ca,transport/'cacert.pem')
(dest/'PROVENANCE.json').write_text(json.dumps({'archives':json.loads((root/'ports-lock.json').read_text()),'audit':report,'target_tested':False,'local_metrics':{'architecture':'x86_64','load_commands':output('otool','-l',str(dest/'system-metrics')),'imports':output('nm','-u',str(dest/'system-metrics'))},'macports_public_key_sha256':__import__('hashlib').sha256((project/'tools/mini_usb/macports-pubkey.pem').read_bytes()).hexdigest()},indent=2)+'\n')
print('Relocated and audited',len(files),'Mach-O files; imported external symbols resolved.')
