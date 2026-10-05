from inspect_ai.solver._multiple_choice import parse_answers
from inspect_ai.solver import TaskState
from inspect_ai.model import ModelOutput
cases=['ANSWER: A','A','The answer is A','ANSWER: a','ANSWER: (A)','ANSWER: A.','ANSWER: A because sunlight','Reasoning\nANSWER: A','ANSWER: B']
for x in cases:
 s=TaskState(model='mockllm/model',sample_id='x',epoch=1,input='q',messages=[],target='A',choices=['x','y','z','w'],output=ModelOutput(model='mockllm/model',completion=x))
 print(repr(x),sorted(parse_answers(s,False)))
