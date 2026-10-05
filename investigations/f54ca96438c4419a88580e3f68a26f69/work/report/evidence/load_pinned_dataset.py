from datasets import load_dataset
import json
rev='210d026faf9955653af8916fad021475a3f00453'
ds=load_dataset('allenai/ai2_arc','ARC-Easy',split='test',revision=rev)
ids={'Mercury_417466','Mercury_7081673','Mercury_7037258','NYSEDREGENTS_2015_4_8','Mercury_7239733'}
selected=[dict(x) for x in ds if x['id'] in ids]
summary={
 'revision':rev,'count':len(ds),'columns':ds.column_names,
 'features':str(ds.features),
 'selected':selected,
 'missing':{c:sum(x[c] is None or x[c]=='' for x in ds) for c in ds.column_names},
 'unique_ids':len(set(ds['id'])),
 'answerKey_counts':{k:ds['answerKey'].count(k) for k in sorted(set(ds['answerKey']))},
 'choice_label_patterns':{},
}
for x in ds:
 p=','.join(x['choices']['label'])
 summary['choice_label_patterns'][p]=summary['choice_label_patterns'].get(p,0)+1
open('/workspace/report/evidence/pinned_dataset_summary.json','w').write(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
