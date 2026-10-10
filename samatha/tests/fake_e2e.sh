#!/bin/bash
# Offline end-to-end test of the samatha engine (fake LLM, temp DB, no Telegram). Usage: bash fake_e2e.sh /path/to/stage
set -e
STAGE=${1:-/root/samatha/stage}
T=$(mktemp -d /tmp/samtest.XXXX)
cp -r "$STAGE/skill" "$T/skill"
export SAMATHA_HOME=$T SAMATHA_SKILL=$T/skill/linkedin SAMATHA_FAKE_LLM=1 SAMATHA_NO_NOTIFY=1 SAMATHA_ENV=/nonexistent
S="/usr/local/lib/hermes-agent/venv/bin/python $STAGE/samatha.py"

echo "== idea (pipeline)"; $S idea "Why I stopped paying for creator databases" --wait | head -12
echo "== pick 2"; $S pick 2 | head -3
echo "== revise 1"; $S revise 1 "shorter" | head -2
echo "== posted (selected draft, unchanged)"; $S posted --url "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/"
echo "== posted again same url"; $S posted --url "https://www.linkedin.com/feed/update/urn:li:activity:7100000000000000001/"
# fixtures: 8 posts over the last weeks. Week offsets relative to this Monday.
MON=$(date -u -d "last monday" +%F); [ "$(date -u +%u)" = 1 ] && MON=$(date -u +%F)
d(){ date -u -d "$MON - $1 days" +%F; }
i=0
for spec in "3:story:build-in-public:1000:50" "4:story:build-in-public:2000:90" "5:data:creator-sourcing:1000:20" \
            "10:data:creator-sourcing:1500:30" "11:contrarian:tool-pricing-opinions:800:40" "17:story:build-in-public:1200:60" \
            "18:question:creator-sourcing:900:9" "24:contrarian:tool-pricing-opinions:1000:15"; do
  IFS=: read off ht pil imp eng <<< "$spec"; i=$((i+1))
  $S posted --url "https://www.linkedin.com/feed/update/urn:li:activity:70000000000000000$i/" --date "$(d $off)T09:00:00" \
     --hook-type $ht --pillar $pil --text "fixture post $i" >/dev/null
  $S metrics "70000000000000000$i" --impressions $imp --engagements $eng --comments $((eng/5)) >/dev/null
done
$S lead "Maya" --post 700000000000000002
echo "== kpi"; $S kpi
echo "== week"; $S week --no-llm
echo "== alerts"; $S alerts
echo "== status"; $S status --json
echo "== calibrate"; $S calibrate
echo "== choose_hooks weighting (100 trials, count of explore/exploit types)"
cd "$STAGE" && SAMATHA_HOME=$T /usr/local/lib/hermes-agent/venv/bin/python -c "
import samatha, collections
hooks=[{'type':t,'text':t} for t in samatha.HOOK_TYPES]
c=collections.Counter()
with samatha.db() as con:
    for _ in range(100):
        p,_=samatha.choose_hooks(con,hooks); c.update(('E:' if e else 'X:')+h['type'] for h,e in p)
print(dict(c))"
echo "TMP=$T"
echo "== import fixtures (xlsx bulk export + Shares.csv)"
/usr/local/lib/hermes-agent/venv/bin/python - "$T" <<'PY'
import sys, zipfile
T=sys.argv[1]
def sheet(rows):
    x=[]
    for i,r in enumerate(rows,1):
        cells="".join(f'<c r="{chr(65+j)}{i}" t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v,str) else f'<c r="{chr(65+j)}{i}"><v>{v}</v></c>' for j,v in enumerate(r) if v is not None)
        x.append(f'<row r="{i}">{cells}</row>')
    return '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+"".join(x)+'</sheetData></worksheet>'
u="https://www.linkedin.com/feed/update/urn:li:activity:7300000000000000001"
top=[["Maximum of 50 posts available"],["Post URL","Post publish date","Engagements",None,"Post URL","Post publish date","Impressions"],
     [u,"10/06/2026",42,None,u,"10/06/2026",1400]]
fol=[["Total followers on 10/9/2026:",500],["Date","New followers"],["10/06/2026",12],["10/07/2026",9]]
z=zipfile.ZipFile(T+"/export.xlsx","w")
z.writestr("xl/workbook.xml",'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="TOP POSTS" sheetId="1" r:id="rId1"/><sheet name="FOLLOWERS" sheetId="2" r:id="rId2"/></sheets></workbook>')
z.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
z.writestr("xl/worksheets/sheet1.xml",sheet(top)); z.writestr("xl/worksheets/sheet2.xml",sheet(fol)); z.close()
open(T+"/Shares.csv","w").write('Date,ShareLink,ShareCommentary,SharedUrl,MediaUrl,Visibility\n2026-10-06 09:12:00,"'+u.replace("feed/update/","feed/update/")+'","'+("I launched the homepage today. The UI looks like it is from 2010. On purpose. "*5)+'",,,MEMBER_NETWORK\n')
PY
$S import-analytics "$T/export.xlsx"
$S import-posts "$T/Shares.csv"
$S kpi | grep -E "followers|Engagement"
ls "$T/skill/linkedin/examples/positive"
echo "== budget guard (cap below today's spend) -> fallback hint"
sed -i 's/^daily_cap_usd: .*/daily_cap_usd: 0.001/' "$T/skill/linkedin/samatha.yaml"
$S idea "anything" --wait
sed -i 's/^daily_cap_usd: .*/daily_cap_usd: 0.012/' "$T/skill/linkedin/samatha.yaml"
echo "== cap hit mid-run"; $S idea "mid-run cap" --wait 2>&1 | head -4
rm -rf "$T"
