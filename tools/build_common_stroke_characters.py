"""Build the 2013 level-one/two scope from a downloaded official Unihan ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
COMMON_COUNT=6500


def build(archive):
    payload=archive.read_bytes()
    indexed={}
    with zipfile.ZipFile(archive) as source:
        text=source.read('Unihan_OtherMappings.txt').decode('utf-8')
        if '# Unicode Version 17.0.0' not in text.splitlines()[:10]:
            raise ValueError('Expected the pinned Unicode 17.0.0 archive')
        for line in text.splitlines():
            if not line or line.startswith('#'):continue
            code,field,value=line.split('\t')
            if field!='kTGH' or not value.startswith('2013:'):continue
            index=int(value.split(':')[1])
            if index in indexed:raise ValueError('Duplicate table index')
            indexed[index]=chr(int(code[2:],16))
    if set(indexed)!=set(range(1,8106)):raise ValueError('Incomplete 2013 table')
    characters=''.join(indexed[i] for i in range(1,COMMON_COUNT+1))
    if len(characters)!=COMMON_COUNT or len(set(characters))!=COMMON_COUNT:raise ValueError('Invalid level-one/two scope')
    return dict(schema='ring-common-stroke-characters',version=2,
        table='通用规范汉字表（2013）一、二级字表',count=COMMON_COUNT,characters=characters,
        charactersSha256=hashlib.sha256(characters.encode('utf-8')).hexdigest(),
        source=dict(url='https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip',
                    unicodeVersion='17.0.0',archiveSha256=hashlib.sha256(payload).hexdigest(),
                    property='kTGH',tableYear=2013,indexFirst=1,indexLast=COMMON_COUNT,
                    documentation='https://unicode.org/reports/tr38/tr38-40.html#kTGH'),
        license='Unicode-3.0; see unicode-license.txt')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'src/proximic_ring/assets/stroke-common-characters.json')
    args=parser.parse_args()
    result=build(args.archive)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"Saved {result['count']} characters: {args.output}")


if __name__=='__main__':main()
