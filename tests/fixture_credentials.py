"""Credential-shaped test inputs, in one home.

detect-secrets anchors each `.secrets.baseline` entry by line number, so a
credential-shaped literal spelled inline in a test module moves every time a
test lands above it and reds the gate on a commit that touched no credential.
Kept here, an entry moves only when this file changes. None of these is a
real credential: each is source text fed to a check under test.
"""

# A plaintext-password comparison, the shape check_plaintext_passwords flags.
PASSWORD_LITERAL_COMPARISON = 'if password == "expected":'
