import json
from typing import Dict, List, Any

def create_hierarchy(criteria: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Restructures a flat list of criteria into a hierarchical structure where children
    are nested under their parents.
    """
    # First create a mapping of id to criteria
    id_mapping = {item['id']: item.copy() for item in criteria}
    
    # Initialize children list for each item
    for item in id_mapping.values():
        item['children'] = []
    
    # Root items that will form our final structure
    root_items = []
    
    # Process each item to build the hierarchy
    for item in criteria:
        current = id_mapping[item['id']]
        
        if item.get('parent') is None:
            # This is a root item
            root_items.append(current)
        else:
            # Add this item to its parent's children
            parent = id_mapping.get(item['parent'])
            if parent:
                parent['children'].append(current)
    
    # Remove empty children arrays
    def cleanup_empty_children(items):
        for item in items:
            if not item['children']:
                del item['children']
            else:
                cleanup_empty_children(item['children'])
    
    cleanup_empty_children(root_items)
    return root_items

def process_json_file(json_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Processes the JSON data and returns a new structure with nested children.
    """
    result = []
    
    for section in json_data:
        # Create a copy of the section without the criteria
        new_section = {
            'section': section['section']
        }
        
        # Process the criteria for this section to create the hierarchy
        new_section['criteria'] = create_hierarchy(section['criteria'])
        
        result.append(new_section)
    
    return result

def main():
    # Read input JSON
    with open('criteria.json', 'r') as f:
        json_data = json.load(f)
    
    # Process the JSON
    restructured_data = process_json_file(json_data)
    
    # Write the restructured JSON to a new file
    with open('criteria_restructured.json', 'w') as f:
        json.dump(restructured_data, f, indent=2)

if __name__ == "__main__":
    main()