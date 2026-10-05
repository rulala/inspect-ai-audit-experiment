from inspect_ai.log import read_eval_log_samples
from pathlib import Path
import json
p=next(Path('/inputs/logs/0').glob('*.eval'))
rows=[]
for s in read_eval_log_samples(str(p)):
    d=s.model_dump(mode='json', exclude_none=False)
    # Retain the audit-relevant exact fields; full objects can contain bulky events.
    rows.append({k:d.get(k) for k in ['id','epoch','input','choices','target','messages','output','scores','error','metadata']})
Path('/workspace/report/evidence/recorded_samples.json').write_text(json.dumps(rows,indent=2,default=str))
for r in rows:
    print('\nID',r['id'],'target',r['target'],'error',r['error'])
    print('input',r['input'])
    print('choices',r['choices'])
    print('messages',r['messages'])
    print('output',r['output'])
    print('scores',r['scores'])
