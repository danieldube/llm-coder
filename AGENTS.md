# Mypy Configuration Guide

## Resolving Missing Library Stubs

When running mypy, you may encounter errors like:
```
error: Library stubs not installed for "requests"  [import-untyped]
```

### Permanent Solution

To permanently resolve this issue, add the missing type stubs to your pre-commit configuration:

1. Edit `.pre-commit-config.yaml`
2. Add `additional_dependencies: [types-requests]` to the mypy hook configuration

Example:
```yaml
repos:
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        args: [--strict, --install-types]
        additional_dependencies: [types-requests]
```

## Best Practices

- Keep type stubs in sync with your actual dependencies
- Regularly update pre-commit hooks to use latest versions
- Consider adding common type stubs to prevent recurring issues
- For heterogeneous JSON/config dictionaries, explicitly annotate the top-level
  value as `dict[str, Any]`; otherwise mypy may infer a common `Collection[str]`
  value type and reject nested dictionary indexing.
