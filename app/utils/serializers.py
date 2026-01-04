"""
Serialization utilities for API responses.

Provides camelCase conversion for frontend compatibility.
The frontend (React Native) uses camelCase, while Python uses snake_case.
"""

import re
from typing import Any, Dict


def snake_to_camel(snake_str: str) -> str:
    """
    Convert snake_case string to camelCase.

    Examples:
        snake_to_camel("user_id") -> "userId"
        snake_to_camel("created_at") -> "createdAt"
        snake_to_camel("partner1_id") -> "partner1Id"
    """
    components = snake_str.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])


def camel_to_snake(camel_str: str) -> str:
    """
    Convert camelCase string to snake_case.

    Examples:
        camel_to_snake("userId") -> "user_id"
        camel_to_snake("createdAt") -> "created_at"
    """
    # Insert underscore before uppercase letters and convert to lowercase
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel_str)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def convert_keys_to_camel(data: Any) -> Any:
    """
    Recursively convert all dictionary keys from snake_case to camelCase.

    Handles nested dictionaries and lists.

    Args:
        data: Dictionary, list, or primitive value

    Returns:
        Same structure with camelCase keys
    """
    if isinstance(data, dict):
        return {
            snake_to_camel(key): convert_keys_to_camel(value)
            for key, value in data.items()
        }
    elif isinstance(data, list):
        return [convert_keys_to_camel(item) for item in data]
    else:
        return data


def convert_keys_to_snake(data: Any) -> Any:
    """
    Recursively convert all dictionary keys from camelCase to snake_case.

    Used for processing incoming requests from frontend.

    Args:
        data: Dictionary, list, or primitive value

    Returns:
        Same structure with snake_case keys
    """
    if isinstance(data, dict):
        return {
            camel_to_snake(key): convert_keys_to_snake(value)
            for key, value in data.items()
        }
    elif isinstance(data, list):
        return [convert_keys_to_snake(item) for item in data]
    else:
        return data


# Field name mappings for special cases
# Maps backend field names to frontend field names
FIELD_MAPPINGS = {
    # group_id is called relationshipId in frontend
    'group_id': 'relationshipId',
}

# Reverse mapping
REVERSE_FIELD_MAPPINGS = {v: k for k, v in FIELD_MAPPINGS.items()}


def apply_field_mappings(data: Any) -> Any:
    """
    Apply custom field name mappings after camelCase conversion.

    This handles special cases where the frontend uses a different
    name than the camelCase version (e.g., groupId -> relationshipId).
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Check if this key needs special mapping
            mapped_key = FIELD_MAPPINGS.get(key, key)
            result[mapped_key] = apply_field_mappings(value)
        return result
    elif isinstance(data, list):
        return [apply_field_mappings(item) for item in data]
    else:
        return data


def serialize_response(data: Any) -> Any:
    """
    Main serialization function for API responses.

    1. Converts snake_case keys to camelCase
    2. Applies custom field mappings

    Usage:
        response_data = serialize_response(model.dict())
    """
    camel_data = convert_keys_to_camel(data)
    return apply_field_mappings(camel_data)
