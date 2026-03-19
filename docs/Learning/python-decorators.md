1. The "Gatekeeper" (Layer 4/5: RBAC & Domain Whitelist)
Scenario: You need to protect specific API endpoints so only users with an @gmail.com email can access them.

The Task: Write a decorator called @require_gmail_domain that inspects a "user" dictionary passed to a function.

The Constraint: If the email doesn't end in @gmail.com, raise a PermissionError.

Goal: Learn how to access and validate function arguments inside a wrapper.

Python
# Exercise 1 Template
def require_gmail_domain(func):
    def wrapper(user, *args, **kwargs):
        # Your logic here
        return func(user, *args, **kwargs)
    return wrapper

@require_gmail_domain
def view_internal_project(user, project_id):
    return f"Accessing project {project_id} for {user['email']}"
2. The "Speed Demon" (Layer 3: Token Caching)
Scenario: Your JWT validation (Layer 2) is cryptographically expensive. You want to cache the result of a "check" so you don't re-run it for the same token within a short window.

The Task: Create a decorator called @token_cache that stores the result of a function in a dictionary.

The Constraint: Use the function's input (the token string) as the key. If the key exists in the cache, return the cached value instead of running the function.

Goal: Understand stateful decorators and basic memoization.

3. The "Auditor" (Debug/Logging)
Scenario: You need to track how long each of your 6 auth layers takes to execute to find bottlenecks.

The Task: Write a decorator @audit_log that prints the name of the function being called and how many milliseconds it took to run.

The Constraint: Use the time module. The decorator should work on any function, regardless of how many arguments it has.

Goal: Master the use of *args and **kwargs for universal decorators.

Python
# Example of what the output should look like:
# [AUDIT] Executing 'validate_jwt'... 
# [AUDIT] 'validate_jwt' finished in 1.2ms