"""Download the pinned release asset through GitHub's API and verify its hash."""
import hashlib
from pathlib import Path
import sys
import time
import urllib.request

RELEASES = {
    '1.88.0': ('https://api.github.com/repos/dolthub/dolt/releases/assets/413875211',
               'aa5256c62cd5665b82dbfac2fafdae120424507ede4b8f23b2b3f09e083a49e2'),
}

def download(version, destination):
    url, expected = RELEASES[version]
    destination=Path(destination)
    for attempt in range(3):
        try:
            digest=hashlib.sha256()
            request=urllib.request.Request(url,headers={'Accept':'application/octet-stream','X-GitHub-Api-Version':'2022-11-28','User-Agent':'investment-data-build'})
            deadline=time.monotonic()+90
            with urllib.request.urlopen(request,timeout=30) as response,destination.open('wb') as output:
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    if time.monotonic()>deadline:raise TimeoutError('Dolt release download deadline exceeded')
                    output.write(chunk);digest.update(chunk)
            if digest.hexdigest()!=expected:
                raise ValueError('Dolt release checksum mismatch')
            return
        except Exception:
            destination.unlink(missing_ok=True)
            if attempt==2:raise
            time.sleep(2)

if __name__=='__main__':download(sys.argv[1],sys.argv[2])
