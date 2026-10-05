import inspect
from inspect_ai.scorer import choice
from inspect_ai.solver import multiple_choice
print('choice public factory:\n',inspect.getsource(choice))
print('\nmultiple_choice public factory:\n',inspect.getsource(multiple_choice))
# Find implementation module contents around parser helpers.
for obj,name in [(choice,'choice'),(multiple_choice,'multiple_choice')]:
 print('\n',name,'file',inspect.getsourcefile(obj))
