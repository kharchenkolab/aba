---
name: register-artifact
description: Add a produced file to the entity graph (figure / table / dataset)
when_to_use: A run produced a file that should become a first-class entity
---

# Register artifact

Handled automatically by the run_python post-tool hook for files
matching known extensions in standard output paths. Only invoke this
skill manually when an artifact lives outside the conventional
locations or needs custom metadata at registration time.
