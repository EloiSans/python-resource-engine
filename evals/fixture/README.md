# Help desk

A small application built with Resource Engine. It has one module,
`customers`, in `src/helpdesk/`. The composition root is
`src/helpdesk/bootstrap.py` and the seed policies are in
`src/helpdesk/authz.py`.

```bash
resource-engine check
resource-engine docs validate --strict
pytest
```
