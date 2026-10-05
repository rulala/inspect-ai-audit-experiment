from inspect_ai.log import read_eval_log, read_eval_log_sample_summaries
from pathlib import Path
import json
p=next(Path('/inputs/logs/0').glob('*.eval'))
h=read_eval_log(str(p), header_only=True)
# Pydantic models vary; JSON mode retains exact header fields.
out={'path':str(p),'header':h.model_dump(mode='json', exclude_none=False)}
s=[]
for x in read_eval_log_sample_summaries(str(p)):
    s.append(x.model_dump(mode='json', exclude_none=False))
out['sample_summaries']=s
Path('/workspace/report/evidence/log_inventory.json').write_text(json.dumps(out,indent=2,default=str))
print('samples',len(s))
print('status',h.status)
print('task',h.eval.task,'model',h.eval.model)
print('results',h.results)
print('ids',[x.get('id') for x in s])
