"""Shared lightweight process evidence; no global client/config initialization."""
import subprocess
import time


class ClientError(RuntimeError):
    pass



def integer(value, minimum=0):
    return type(value) is int and value >= minimum



def remaining(deadline, limit=5):
    if deadline is None:
        return limit
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise ClientError('OBSERVATION_INCOMPLETE')
    return min(limit, budget)



def process_start(pid, *, deadline=None):
    if not integer(pid,1):
        return None
    try:
        result = subprocess.run(['ps','-p',str(pid),'-o','lstart='], capture_output=True, text=True, timeout=remaining(deadline, 3))
        if result.returncode == 1 and not result.stderr:
            return 'absent'
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None
