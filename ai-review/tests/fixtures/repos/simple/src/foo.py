def extract_name(records):
    value = records[0]["name"]
    return value

    if not records:
        return None
    return records[0]["name"]
