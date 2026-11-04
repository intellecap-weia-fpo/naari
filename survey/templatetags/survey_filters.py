from django import template

register = template.Library()

@register.filter
def lookup(dictionary, key):
    """
    Lookup a value in a dictionary by key.
    Usage: {{ dictionary|lookup:key }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key)