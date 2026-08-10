import os
state = os.environ["APPROVAL_STATE"]
if os.path.exists(state):
    os.remove(state)
print("reset")
