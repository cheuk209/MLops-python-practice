from typing import Dict

def require_gmail_domain(func):
    def wrapper(user: Dict, *args, **kwargs):
        # *args and **kwargs are used when we do not know
        # how many arguments a user might pass to function
        # * is unpacking operator, it will put everything into tuple
        # ** is unpacking operator, to put everything into dict
        if "@gmail.com" not in user['email']:
            raise PermissionError("This is not an gmail user")
        return func(user, *args, **kwargs)
    return wrapper

@require_gmail_domain
def view_internal_project(user, project_id):
    return f"Accessing project {project_id} for {user['email']}"

def token_cache(func):
    cache = {}
    def wrapper(token: str):
        if token in cache:
            print("Token found in cache")
            return cache[token]
        else:
            print("Token not found in cache, calling function")
            result = func(token)
            cache[token] = result
            return result
    return wrapper
