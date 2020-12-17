import os, sys
from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, OWL, NamespaceManager, Namespace
from io import StringIO
from lxml import etree

def main():
    if len(sys.argv) != 4:
        sys.exit(
            'Incorrect number of arguments: {}\nUsage: python CityGML2RDF.py [input ontology paths] [input datafile] [output file]\nontology input paths are separated by a ","'.format(
                len(sys.argv)))

    ############################
    ##  Initialize variables  ##
    ############################

    global input_tree
    global ontology
    global output_graph
    global output_uri
    global id_count
    global class_definition_cache
    global datatype_definition_cache
    global objectproperty_definition_cache
    global datatypeproperty_definition_cache
    global namespace_mappings
    global parsed_nodes
    global GML
    global GeoSPARQL
    global log

    filename = os.path.split(sys.argv[2])[-1].split('.')[0]
    input_tree = etree.parse( StringIO(sys.argv[2]) )
    output_graph = Graph()
    output_uri = 'https://github.com/VCityTeam/UD-Graph/{}'.format(filename)
    log = ''

    # input_node_count  = 0
    # output_node_count = 0
    # current_node      = 0
    id_count = {}
    class_definition_cache = {}
    datatype_definition_cache = {}
    objectproperty_definition_cache = {}
    datatypeproperty_definition_cache = {}
    parsed_nodes = []
    namespace_mappings = {
        'http://www.opengis.net/citygml/2.0': ['http://www.opengis.net/citygml/2.0/core#'],
        'http://www.opengis.net/citygml/appearance/2.0': ['http://www.opengis.net/citygml/2.0/appearance#'],
        'http://www.opengis.net/citygml/building/2.0': ['http://www.opengis.net/citygml/2.0/building#'],
        'http://www.opengis.net/citygml/bridge/2.0': ['http://www.opengis.net/citygml/2.0/bridge#'],
        'http://www.opengis.net/citygml/generics/2.0': ['http://www.opengis.net/citygml/2.0/generics#'],
        'http://www.opengis.net/citygml/relief/2.0': ['http://www.opengis.net/citygml/2.0/relief#'],
        'http://www.opengis.net/citygml/transportation/2.0': ['http://www.opengis.net/citygml/2.0/transportation#'],
        'http://www.opengis.net/citygml/cityobjectgroup/2.0': ['http://www.opengis.net/citygml/2.0/cityobjectgroup#'],
        'http://www.opengis.net/citygml/landuse/2.0': ['http://www.opengis.net/citygml/2.0/landuse#'],
        'http://www.opengis.net/citygml/tunnel/2.0': ['http://www.opengis.net/citygml/2.0/tunnel#'],
        'http://www.opengis.net/citygml/cityfurniture/2.0': ['http://www.opengis.net/citygml/2.0/cityfurniture#'],
        'http://www.opengis.net/citygml/vegetation/2.0': ['http://www.opengis.net/citygml/2.0/vegetation#'],
        'http://www.opengis.net/citygml/waterbody/2.0': ['http://www.opengis.net/citygml/2.0/waterbody#'],
        'http://www.opengis.net/gml': ['http://www.opengis.net/ont/gml#', 'http://def.isotc211.org/iso19136/2007/GML#']
    }

    # compile ontology
    print('Compiling Ontology...')
    ontology = Graph()
    for path in sys.argv[1].split(','):
        if path.startswith('http://'):
            print('  ' + path)
            ontology.parse(path, format='xml')
            continue
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.endswith('.ttl'):
                    print('  ' + file)
                    ontology.parse(os.path.join(root, file), format='turtle')
                if file.endswith('.rdf'):
                    print('  ' + file)
                    ontology.parse(os.path.join(root, file), format='xml')
    # copy ontology namespace bindings to output graph namespace manager, add
    # default output namespace binding, and set geospatial namespaces
    output_graph.namespace_manager = NamespaceManager(ontology)
    output_graph.namespace_manager.bind('data', output_uri + '#')
    GML = Namespace('http://www.opengis.net/ont/gml#')
    GeoSPARQL = Namespace('http://www.opengis.net/ont/geosparql#')

    ###################################
    ##  Convert input file into rdf  ##
    ###################################

    print('Converting XML tree...')
    if input_tree.getroot().attrib.get('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation') is not None:
        input_tree.getroot().attrib.pop('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation')
    for input_node in input_tree.getroot().iter():
        # skip comment nodes
        if not isinstance(input_node.tag, str):
            continue
        # skip already parsed nodes
        if input_tree.getelementpath(input_node) in parsed_nodes:
            continue

        # if the node is a class, generate an id and individual for it.
        if isClass( input_node.tag ):
            generateIndividual(input_node)

    print('Writing graph to disk...')
    # print('{}/{}.rdf'.format(sys.argv[3], filename))
    # with open('{}/{}.rdf'.format(sys.argv[3], filename), 'wb') as file:
    #     file.write(output_graph.serialize(format='turtle'))
    print('{}/{}.ttl'.format(sys.argv[3], filename))
    with open('{}/{}.ttl'.format(sys.argv[3], filename), 'wb') as file:
        file.write(output_graph.serialize(format='turtle'))
    print('Writing log to log.txt ...')
    with open('log.txt', 'w') as file:
        file.write(log)



######################################
##  Graph transformation functions  ##
######################################

# generate a new individual from an XML node and its children, then add the
# node to the output graph. An id is returned for recursive calls
def generateIndividual(node):
    # skip node if already parsed
    if input_tree.getelementpath(node) in parsed_nodes:
        return
    global log
    node_tag = mapNamespace(node)
    node_id = URIRef('{}#{}'.format(output_uri, node.attrib['{http://www.opengis.net/gml}id'])) if\
        '{http://www.opengis.net/gml}id' in node.attrib else URIRef(generateID(node_tag))
    output_graph.add( (node_id, RDF.type, OWL.NamedIndividual) )
    output_graph.add( (node_id, RDF.type, node_tag) )

    # if the node is a geometry node, create a gml serialization and add
    # descendant nodes to the parsed_nodes list
    if isGeometry(node.tag):
        generateGeometrySerialization(node, node_id)
        parsed_nodes.append(input_tree.getelementpath(node))
        return node_id

    for child in node:
        # check if child node is a class. If so, generate a new individual for the
        # child and create an object property linking the two individuals.
        if isClass(child.tag):
            child_id = generateIndividual(child)
            objectproperties = findObjectProperty(node.tag, child.tag)
            for objectproperty in objectproperties:
                output_graph.add( (node_id, objectproperty, child_id) )
            if isGeometry(child.tag):
                output_graph.add( (node_id, GeoSPARQL.hasGeometry, child_id) )
        # check if child node is a datatype. If so, generate a datatype for the
        # child and create a datatype property linking the individual and datatype.
        elif isDatatype(child.tag):
            child_text = Literal(child.text)
            datatypeproperty = findDatatypeProperty(node.tag, child.tag)
            for property in datatypeproperty:
                output_graph.add( (node_id, property, child_text) )
        # check if child node is an object property. If so, generate the object
        # property nodes and their corresponding individuals by calling
        # generateObjectProperties().
        elif isObjectProperty(child.tag):
            generateObjectProperties(node, node_id, child)
        # check if child node is an datatype property. If so, generate the datatype
        # property nodes and their corresponding individuals by calling
        # generateDatatypeProperty().
        elif isDatatypeProperty(child.tag):
            generateDatatypeProperty(node, node_id, child)
        else:
            log += 'Error! Unknown XML element: {}\n'.format( input_tree.getelementpath(child) )

    for attribute in node.attrib:
        attribute_tag = mapNamespace(attribute)
        attribute_text = Literal(node.attrib[attribute])
        output_graph.add( (node_id, attribute_tag, attribute_text) )

    # when complete, add node to parsed nodes list
    parsed_nodes.append(input_tree.getelementpath(node))
    return node_id


# Generate an individual for each child (which should all be classes if input
# tree is well formed) and create an object property linking the parent
# individual with each child individual.
def generateObjectProperties(parent, parent_id, node):
    global log
    for child in node:
        # check if child node is a class. If so, generate a new individual for the
        # child and create an object property linking the two individuals. In the
        # case the child is geometry, generate a geometry serialization.
        if isClass(child.tag):
            child_id = generateIndividual(child)
            property = findObjectProperty(parent.tag, child.tag, node.tag)
            if property is not None:
                output_graph.add((parent_id, property, child_id))
            else:
                log += 'Error! Object property not found: {}\n'.format(
                    input_tree.getelementpath(node) )
            # check if child is a gml geometry node. If so, generate the geometry
            # property gsp:hasGeometry.
            if isGeometry(child.tag):
                output_graph.add( (parent_id, GeoSPARQL.hasGeometry, child_id) )
        else:
            log += 'Error! Class element for object property not found: {}\n'.format(
                input_tree.getelementpath(child) )


# Generate a datatype for each child (which should all contain datatype literals
# if well formed) and create a datatype property linking the parent individual
# with each child datatype literal.
def generateDatatypeProperty(parent, parent_id, node):
    global log
    # check if child node is a datatype. If so, generate a new datatype literal
    # for the child and create a datatype property linking the class with the
    # datatype literal.
    if node.text is not None:
        property = findDatatypeProperty(parent.tag, node.tag)
        if property is not None:
            output_graph.add((parent_id, property, Literal(node.text)))
        else:
            log += 'Error! Datatype property not found: {}\n'.format(
                input_tree.getelementpath(node) )
    else:
        log += 'Error! Datatype text for datatype property not found: {}\n'.format(
            input_tree.getelementpath(node) )


# Generate the gsp:gmlLiteral serialization of a geometry node
def generateGeometrySerialization(node, node_id):
    geometry = str(etree.tostring(node, pretty_print=False))[2:-1].replace(
        '\\n', '').replace('  ', '').strip()
    # xlinks are not yet supported by parliament for gsp:gmlLiterals
    if 'xlink:href' in geometry:
        return

    serialization = Literal(geometry, datatype=GeoSPARQL.gmlLiteral)
    output_graph.add( (node_id, GeoSPARQL.asGML, serialization) )



#########################
##  Utility functions  ##
#########################

# find an object property which links (intersects) two given classes based on the
# domain and range of the property.
def findObjectProperty(tag1, tag2, property_tag=None):
    global log
    qname1 = etree.QName(tag1)
    qname2 = etree.QName(tag2)
    if property_tag is None:
        query = ontology.query('''
            SELECT DISTINCT ?objectproperty
            WHERE {
                {   ?objectproperty a owl:ObjectProperty ;
                        rdfs:domain ?domain ;
                        rdfs:range  ?range .
                    <%s> rdfs:subClassOf* ?domain .
                    <%s> rdfs:subClassOf* ?range .
                }
                UNION
                {   <%s> a owl:Class ;
                        rdfs:subClassOf [ a owl:Restriction ;
                                          owl:allValuesFrom <%s> ;
                                          owl:onProperty    ?objectproperty 
                                        ] .
                }
            }''' % (mapNamespace(qname1),
                    mapNamespace(qname2),
                    mapNamespace(qname1),
                    mapNamespace(qname2)))
        if len(query) > 0:
            return query
    else:
        for property in getObjectProperties(property_tag):
            query = ontology.query('''
                ASK   {
                    { <%s> a owl:ObjectProperty ;
                            rdfs:domain ?domain ;
                            rdfs:range  ?range .
                        <%s> rdfs:subClassOf* ?domain .
                        <%s> rdfs:subClassOf* ?range .
                    }
                    UNION
                    { <%s> a owl:Class ;
                        rdfs:subClassOf [ a owl:Restriction ;
                                          owl:allValuesFrom <%s> ;
                                          owl:onProperty    <%s> 
                                        ] .
                    }
                }''' % (property[0],
                        mapNamespace(qname1),
                        mapNamespace(qname2),
                        mapNamespace(qname1),
                        mapNamespace(qname2),
                        property[0]) )
            if bool(query):
                return property[0]
    log += 'Error! No matching object property found between: {}, {}\n'.format(
        tag1, tag2)
    return None


# find a datatype property which links (intersects) a given class and a datatype
# based on the domain and range of the property or which a given class contains
# a universal restriction of the property.
def findDatatypeProperty(tag1, property_tag=None):
    global log
    qname1 = etree.QName(tag1)
    if isClass(tag1):
        if property_tag is None:
            query = ontology.query('''
                SELECT DISTINCT ?datatypeproperty
                WHERE {
                    { ?datatypeproperty a owl:DatatypeProperty ;
                            rdfs:domain ?domain .
                        <%s> rdfs:subClassOf* ?domain .
                    }
                    UNION
                    { <%s> a owl:Class ;
                        rdfs:subClassOf [ a owl:Restriction ;
                                          owl:onProperty    ?datatypeproperty 
                                        ] .
                    }
                }''' % (mapNamespace(qname1),
                        mapNamespace(qname1)) )
            if len(query) > 0:
                return query
        else:
            for property in getDatatypeProperties(property_tag):
                query = ontology.query('''
                    ASK   {
                        { <%s> a owl:DatatypeProperty ;
                                rdfs:domain ?domain .
                            <%s> rdfs:subClassOf* ?domain .
                        }
                        UNION
                        { <%s> a owl:Class ;
                            rdfs:subClassOf [ a owl:Restriction ;
                                              owl:onProperty <%s> 
                                            ] .
                        }
                    }''' % (property[0],
                            mapNamespace(qname1),
                            mapNamespace(qname1),
                            property[0]) )
                if bool(query):
                    return property[0]
    log += 'Error! No matching datatype property found between: {}, {}\n'.format(
        tag1, property_tag)
    return None


# normalize an XML tag namespace for OWL. If input tag namespace is in namespace mappings,
# return the target mapping namespace. Tags are returned as rdflib.URIRef objects.
def mapNamespace(node):
    if str(node) == 'srsName' or str(node) == 'srsDimension' or str(node) == 'uom':
        node = '{http://www.opengis.net/gml}' + str(node)
    qname = etree.QName(node)

    if qname.namespace in namespace_mappings.keys():
        if len(namespace_mappings[qname.namespace]) == 1:
            return URIRef(namespace_mappings[qname.namespace][0] + qname.localname)
        else:
            # TODO: implement dynamic namespace resolution for multiple mappings
            return URIRef(namespace_mappings[qname.namespace][0] + qname.localname)
    elif qname.namespace[-1] == '#':
        return URIRef(qname.namespace + qname.localname)
    elif qname.namespace[-1] == '/':
        return URIRef(qname.namespace[:-1] + '#' + qname.localname)
    else:
        return URIRef(qname.namespace + '#' + qname.localname)


# create a new, unique id from a normalized XML node tag
def generateID(tag):
    name = str(tag).split('#')[-1]
    if name in id_count:
        id_count[name] += 1
        return '{}#{}_{}'.format(output_uri, name, id_count[name])
    else:
        id_count[name] = 0
        return '{}#{}_0'.format(output_uri, name)


# return whether class definition exists in ontology
def isClass(tag):
    qname = etree.QName(tag)
    if tag in class_definition_cache.keys():
        return len(class_definition_cache.get(tag)) > 0
    # TODO: optimize query
    query = []
    for namespace in namespace_mappings[qname.namespace]:
        for line in ontology.query('''
                SELECT DISTINCT ?class
                WHERE {
                    ?class rdf:type owl:Class .
                    FILTER ( STR(?class) = "%s%s" )
                }''' % (namespace, qname.localname) ):
            query.append(line)
    class_definition_cache[tag] = query
    return len(class_definition_cache.get(tag)) > 0


# check if uri corresponds to a class and return the possible classes
def getClasses(tag):
    if isClass(tag):
        return class_definition_cache.get( str(tag) )


# return whether object property definition exists in ontology. Local property
# names may require searching through the shapechange [[name]] descriptor target
# (rdfs:label by default) depending on shapechange property encoding
# configurations.
def isObjectProperty(tag):
    qname = etree.QName(tag)
    if tag in objectproperty_definition_cache.keys():
        return len(objectproperty_definition_cache.get(tag)) > 0
    # TODO: optimize query
    query = []
    for namespace in namespace_mappings[qname.namespace]:
        for line in ontology.query('''
                SELECT DISTINCT ?objectproperty
                WHERE {
                    ?objectproperty rdf:type owl:ObjectProperty .
                    FILTER regex(STR(?objectproperty), "^%s.*.%s$")
                }''' % (namespace, qname.localname) ):
            query.append(line)
    objectproperty_definition_cache[tag] = query
    return len(objectproperty_definition_cache.get(tag)) > 0


# check if uri corresponds to an object property and return the possible properties
def getObjectProperties(tag):
    if isObjectProperty(tag):
        return objectproperty_definition_cache.get( str(tag) )


# return whether datatype property definition exists in ontology. Local property
# names may require searching through the shapechange [[name]] descriptor target
# (rdfs:label by default) depending on shapechange property encoding
# configurations.
def isDatatypeProperty(tag):
    qname = etree.QName(tag)
    if tag in datatypeproperty_definition_cache.keys():
        return len(datatypeproperty_definition_cache.get(tag)) > 0
    # TODO: optimize query
    query = []
    for namespace in namespace_mappings[qname.namespace]:
        for line in ontology.query('''
                SELECT DISTINCT ?datatypeproperty
                WHERE {
                    ?datatypeproperty rdf:type owl:DatatypeProperty .
                    FILTER regex(STR(?datatypeproperty), "^%s.*.%s$")
                }''' % (namespace, qname.localname) ):
            query.append(line)
    datatypeproperty_definition_cache[tag] = query
    return len(datatypeproperty_definition_cache.get(tag)) > 0


# check if uri corresponds to an datatype property and return the possible properties
def getDatatypeProperties(tag):
    if isDatatypeProperty(tag):
        return datatypeproperty_definition_cache.get( str(tag) )


# return whether datatype definition exists in ontology
def isDatatype(tag):
    qname = etree.QName(tag)
    if tag in datatype_definition_cache.keys():
        return len(datatype_definition_cache.get(tag)) > 0
    # TODO: optimize query
    query = []
    for namespace in namespace_mappings[qname.namespace]:
        for line in ontology.query('''
                SELECT DISTINCT ?datatype
                WHERE {
                    ?datatype rdf:type rdfs:Datatype .
                    FILTER ( STR(?datatype) = "%s%s" )
                }''' % (namespace, qname.localname) ):
            query.append(line)
    datatype_definition_cache[tag] = query
    return len(datatype_definition_cache.get(tag)) > 0


# check if uri corresponds to a datatype and return the possible datatypes
def getDatatype(tag):
    if isDatatype(tag):
        return datatype_definition_cache.get( str(tag) )


# return whether node-tree is gml. Convert the uri into a qname
# tuple of (prefix, namespace, localname) to extract the namespace
def isGeometry(tag):
    qname = mapNamespace(tag).split('#')
    if qname[0] + '#' == str(GML) and isClass(tag):
        return ontology.query('''
                ASK {
                    <%s%s> rdfs:subClassOf* gml:AbstractGeometry .
                }''' % (str(GML), qname[1]) )


if __name__ == "__main__":
    main()