from xml.etree import ElementTree


def indent_tree(tree, space="  ", level=0):
    # Taken from python 3.9.0 preview  #TODO: remove after migration to 3.9.0
    """Indent an XML document by inserting newlines and indentation space
    after elements.
    *tree* is the ElementTree or Element to modify.  The (root) element
    itself will not be changed, but the tail text of all elements in its
    subtree will be adapted.
    *space* is the whitespace to insert for each indentation level, two
    space characters by default.
    *level* is the initial indentation level. Setting this to a higher
    value than 0 can be used for indenting subtrees that are more deeply
    nested inside of a document.
    """
    if isinstance(tree, ElementTree.ElementTree):
        tree = tree.getroot()
    if level < 0:
        raise ValueError(f"Initial indentation level must be >= 0, got {level}")
    if not len(tree):
        return

    # Reduce the memory consumption by reusing indentation strings.
    indentations = ["\n" + level * space]

    def _indent_children(elem, level):
        # Start a new indentation level for the first child.
        child_level = level + 1
        try:
            child_indentation = indentations[child_level]
        except IndexError:
            child_indentation = indentations[level] + space
            indentations.append(child_indentation)

        if not elem.text or not elem.text.strip():
            elem.text = child_indentation

        for child in elem:
            if len(child):
                _indent_children(child, child_level)
            if not child.tail or not child.tail.strip():
                child.tail = child_indentation

        # Dedent after the last child by overwriting the previous indentation.
        if not child.tail.strip():
            child.tail = indentations[level]

    _indent_children(tree, 0)


def pprint_xml_tree(tree: ElementTree, indent=2) -> str:
    """Pretty prints an XML ElementTree to a string"""
    indent_tree(tree, space=" " * indent)
    return ElementTree.tostring(tree.getroot()).decode("utf-8").replace("><", ">\n<")
