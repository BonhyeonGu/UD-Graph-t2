import os
import json
import logging
import argparse
from copy import deepcopy
from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, OWL, XSD, NamespaceManager, Namespace
from lxml import etree

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('input_file', help='Specify the input CityGML datafile')
    parser.add_argument('input_model', help='Specify the ontology input path; for multiple ontologies, input paths are separated by a ","')
    parser.add_argument('mapping_file', help='Specify the namespace mapping file')
    parser.add_argument('--output', default='.', help='Specify the output directory')
    parser.add_argument('--format', default='ttl', choices=['ttl', 'rdf'], help='Specify the output data format [rdf, ttl]')
    parser.add_argument('--log', default='output.log', help='Specify the logging file')
    parser.add_argument('--atomic-geometry', action='store_true', help='Iterate into GML geometry to create individuals for each atomic GML element')
    parser.add_argument('--deep-geometry', action='store_true', help='Iterate into GML geometry xlinks to and copy the destination GML into the geosparql:asGML property object')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose console logging')
    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s',
                        filename=args.log,
                        level=logging.DEBUG)

    transformer = CityGML2RdfTransformer(args)
    transformer.transformCityGMLToRdf()
    transformer.writeOutputToFile()

class CityGML2RdfTransformer():

    def __init__(self, args) -> None:
        '''Initialize variables, input and output graphs, mapping ontology, and
        configuration file using the given command line arguments.'''
        super().__init__()
        self.args = args
        self.filename = '.'.join(os.path.split(args.input_file)[-1].split('.')[:-1])
        parser = etree.XMLParser(remove_comments=True)
        self.input_tree = etree.parse(args.input_file, parser)
        self.input_root = self.input_tree.getroot()

        self.output_uri = f'https://github.com/VCityTeam/UD-Graph/{self.filename}'
        self.output_graph = Graph()

        self.id_count = {}
        self.class_definition_cache = {}
        self.datatype_definition_cache = {}
        self.objectproperty_definition_cache = {}
        self.datatypeproperty_definition_cache = {}
        self.input_node_count = 0
        self.input_node_total = 0
        self.valid_geometry = {'MultiPoint', 'MultiCurve', 'MultiSurface', 'MultiGeometry',
        'Point', 'LineString', 'Curve', 'Polygon', 'Surface', 'Solid'}
        self.parsed_nodes = []

        for _ in self.input_root.iter(): # get input node total
            self.input_node_total += 1

        print('Parsing mapping file...')
        with open(self.args.mapping_file, 'r') as file:
            mappings_as_json = json.loads(file.read())
            self.namespace_mappings = mappings_as_json['namespace-mappings']
            self.rdf_mappings =       mappings_as_json['rdf-mappings']

        # compile ontology
        print('Compiling mapping ontology(ies)...')
        self.ontology = Graph()
        for path in self.args.input_model.split(','):
            if path.startswith('http://') or path.startswith('https://'):
                if self.args.verbose:
                    print('  ' + path)
                self.ontology.parse(path, format='xml')
                continue
            for root, dirs, files in os.walk(path):
                for file in files:
                    if file.endswith('.ttl'):
                        if self.args.verbose:
                            print('  ' + file)
                        self.ontology.parse(os.path.join(root, file), format='turtle')
                    if file.endswith('.rdf'):
                        if self.args.verbose:
                            print('  ' + file)
                        self.ontology.parse(os.path.join(root, file), format='xml')
        # copy ontology namespace bindings to output graph namespace manager, add
        # default output namespace binding, and set geospatial namespaces
        self.output_graph.namespace_manager = NamespaceManager(self.ontology)
        self.output_graph.namespace_manager.bind('data', self.output_uri + '#')

        self.GML_NAMESPACE = Namespace(self.input_root.nsmap.get('gml'))
        self.GML_ONT_NAMESPACE = Namespace('http://www.opengis.net/ont/gml#')
        self.GeoSPARQL_NAMESPACE = Namespace('http://www.opengis.net/ont/geosparql#')

    ######################################
    ##  Graph transformation functions  ##
    ######################################

    def transformCityGMLToRdf(self):
        '''Convert input file into rdf'''

        print('Declaring ontology imports...')
        self.output_graph.add( (URIRef(self.output_uri), RDF.type, OWL.Ontology) )
        for ontology_uri in self.ontology.query('''
                SELECT DISTINCT ?ontology
                WHERE { ?ontology a owl:Ontology . }'''):
            self.output_graph.add( (URIRef(self.output_uri), OWL.imports, ontology_uri[0]) )

        print('Converting XML tree...')
        self.updateProgressBar()
        if self.input_root.attrib.get('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation') is not None:
            self.input_root.attrib.pop('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation')
        for input_node in self.input_root.iter():
            # skip already parsed nodes
            if self.input_tree.getelementpath(input_node) in self.parsed_nodes:
                continue

            # if the node is a class, generate an id and individual for it.
            if self.isClass( input_node.tag ):
                self.generateIndividual(input_node)


    def writeOutputToFile(self):
        print('\nWriting graph to disk...')
        if self.args.format == 'rdf':
            full_filepath = os.path.normpath(f'{self.args.output}/{self.filename}.rdf')
            print(full_filepath)
            self.output_graph.serialize(destination=full_filepath, format='xml')
        else:
            full_filepath = os.path.normpath(f'{self.args.output}/{self.filename}.ttl')
            print(full_filepath)
            self.output_graph.serialize(destination=full_filepath, format='turtle')


    def generateIndividual(self, node):
        '''Generate a new individual from an XML node and its children, then add the
        node to the output graph. An id is returned for recursive calls'''
        # TODO: implement rdf mappings for individual and children

        # skip node if already parsed
        if self.input_tree.getelementpath(node) in self.parsed_nodes:
            return

        mapped_tag = ''
        node_id = ''
        mapped_tag = self.mapNamespace(node.tag)
        # if a gml:id is detected, use it in the URI of the individual
        if '{%s}id' % self.GML_NAMESPACE in node.attrib:
            node_id = URIRef(f'{self.output_uri}#' + node.attrib.get('{%s}id' % self.GML_NAMESPACE))
        else:
            node_id = URIRef(self.generateID(mapped_tag))
        self.output_graph.add( (node_id, RDF.type, OWL.NamedIndividual) )
        self.output_graph.add( (node_id, RDF.type, mapped_tag) )

        # if the node is a geometry node, create a gml serialization and add it as a
        # triple to the output graph. All descendant nodes are assumed to be part of
        # the same geometry and therefore are not necessary to parse beyond this step.
        if self.isGeometry(node.tag):
            geometry_literal = self.generateGeometryLiteral(node)
            geometry_node = Literal(geometry_literal, datatype=self.GeoSPARQL_NAMESPACE.gmlLiteral)
            self.output_graph.add( (node_id, self.GeoSPARQL_NAMESPACE.asGML, geometry_node) )

        for attribute in node.attrib:
            if attribute in self.rdf_mappings:
                attribute_tag = URIRef(self.rdf_mappings[attribute])
                attribute_text = Literal(node.attrib[attribute])
                self.output_graph.add( (node_id, attribute_tag, attribute_text) )
            elif self.isDatatypeProperty(attribute, node.tag):
                for property in self.getDatatypeProperties(attribute):
                    for datatype in self.getDatatypePropertyRange(property):
                        attribute_text = Literal(node.attrib[attribute],
                                                datatype=datatype[0])
                    self.output_graph.add((node_id, property, attribute_text))
            elif self.isDatatype(attribute):
                for property in self.findDatatypeProperty(attribute, node.tag):
                    for datatype in self.getDatatypePropertyRange(property):
                        attribute_text = Literal(node.attrib[attribute],
                                                datatype=datatype[0])
                    self.output_graph.add((node_id, property, attribute_text))
            else:
                # TODO: add dynamic datatype detection
                attribute_tag = self.mapNamespace(attribute)
                attribute_text = Literal(node.attrib[attribute])
                self.output_graph.add( (node_id, attribute_tag, attribute_text) )
                logging.warning(f'No datatype or datatype property found for attribute {attribute}, at {self.input_tree.getelementpath(node)}')

        for child in node:
            # if child.tag has an rdf mapping, replace the tag with the mapping.
            mapped_child_tag = child.tag
            if child.tag in self.rdf_mappings:
                mapped_child_tag = etree.QName( self.uriToLXML(self.rdf_mappings[child.tag]) )
            # check if child node is a class. If so, generate a new individual for the
            # child and create an object property linking the two individuals.
            if self.isClass(mapped_child_tag):
                child_id = self.generateIndividual(child)
                objectproperties = self.findObjectProperty(node.tag, mapped_child_tag)
                for objectproperty in objectproperties:
                    self.output_graph.add( (node_id, objectproperty, child_id) )
                if self.isGeometry(mapped_child_tag):
                    self.output_graph.add( (node_id, self.GeoSPARQL_NAMESPACE.hasGeometry, child_id) )
            # check if child node is a datatype. If so, generate a datatype for the
            # child and create a datatype property linking the individual and datatype.
            elif self.isDatatype(mapped_child_tag):
                for property in self.findDatatypeProperty(node.tag, mapped_child_tag):
                    for datatype in self.getDatatypePropertyRange(property):
                        child_text = Literal(child.text, datatype=datatype[0])
                        self.output_graph.add( (node_id, property, child_text) )
            # check if child node is an object property. If so, generate the object
            # property nodes and their corresponding individuals by calling
            # generateObjectProperties().
            elif self.isObjectProperty(mapped_child_tag, node.tag):
                self.generateObjectProperties(node, node_id, child)
            # check if child node is an datatype property. If so, generate the datatype
            # property nodes and their corresponding individuals by calling
            # generateDatatypeProperty().
            elif self.isDatatypeProperty(mapped_child_tag, node.tag):
                self.generateDatatypeProperty(node, node_id, child)
            elif self.isAnnotationProperty(mapped_child_tag):
                annotation_tag = URIRef(mapped_child_tag.namespace + mapped_child_tag.localname)
                annotation_text = Literal(child.text, datatype=XSD.string)
                self.output_graph.add( (node_id, annotation_tag, annotation_text) )
            else:
                logging.warning(f'No mapping found for XML element: {mapped_child_tag}')

        # when complete, add node to parsed nodes list
        self.parsed_nodes.append(self.input_tree.getelementpath(node))
        self.updateProgressBar(node.tag)
        return node_id


    def generateObjectProperties(self, parent, parent_id, node):
        '''Generate an individual for each child (which should all be classes if input
        tree is well formed) and create an object property linking the parent
        individual with each child individual. Also check for xlinks.'''

        if node.attrib.get('{http://www.w3.org/1999/xlink}href') is not None:
            # Check if an xlink is present. If so create a triple referencing the resource
            property = self.findObjectProperty(parent.tag, None, node.tag)
            if property is not None:
                reference = URIRef(f'{self.output_uri}#' +
                    node.attrib['{http://www.w3.org/1999/xlink}href'].split('#')[-1])
                self.output_graph.add( (parent_id, property, reference) )
        for child in node:
            # check if child node is a class. If so, generate a new individual for the
            # child and create an object property linking the two individuals. In the
            # case the child is geometry, generate a geometry serialization.
            if self.isClass(child.tag):
                child_id = self.generateIndividual(child)
                property = self.findObjectProperty(parent.tag, child.tag, node.tag)
                if property is not None:
                    self.output_graph.add((parent_id, property, child_id))
                else:
                    logging.warning(f'Object property, {node.tag}, not found for child, {child.tag}, '
                        f'at: {self.input_tree.getelementpath(node)}')
                # check if child is a gml geometry node. If so, generate the geometry
                # property gsp:hasGeometry.
                if self.isGeometry(child.tag):
                    self.output_graph.add( (parent_id, self.GeoSPARQL_NAMESPACE.hasGeometry, child_id) )
            else:
                logging.warning(f'Child class element, {child.tag}, for object property, {node.tag}, '
                    f'generation not found at: {self.input_tree.getelementpath(child)}')


    def generateDatatypeProperty(self, parent, parent_id, node):
        '''Generate a datatype for each child (which should all contain datatype literals
        if well formed) and create a datatype property linking the parent individual
        with each child datatype literal.'''

        # check if child node is a datatype. If so, generate a new datatype literal
        # for the child and create a datatype property linking the class with the
        # datatype literal.
        if node.text is None:
            logging.warning(f'Datatype text for datatype property not found: {self.input_tree.getelementpath(node)}')
        else:
            property = self.findDatatypeProperty(parent.tag, node.tag)
            if property is not None:
                for datatype in self.getDatatypePropertyRange(property):
                    self.output_graph.add((parent_id, property,
                                    Literal(node.text, datatype=datatype[0])))
            else:
                logging.warning(f'Datatype property not found: {self.input_tree.getelementpath(node)}')


    def generateGeometryLiteral(self, node):
        '''Generate the gsp:gmlLiteral serialization of a geometry node'''
        node_copy = deepcopy(node)

        if self.args.deep_geometry:
            # gather geometry referenced though xlinks 
            for xlink in node_copy.findall('.//*[@{http://www.w3.org/1999/xlink}href]'):
                reference = xlink.attrib.get('{http://www.w3.org/1999/xlink}href').split('#')[-1]
                reference_node = self.input_root.find('.//*[@{%s}id = "%s"]' % (self.GML_NAMESPACE, reference))
                if reference_node is not None:
                    # logging.info(f'Compiling geometry for xlink reference to: {reference}')
                    new_element = etree.Element(xlink.tag)
                    new_element.append(deepcopy(reference_node))
                    parent = xlink.getparent()
                    parent.append(new_element)
                    parent.remove(xlink)

        geometry = str(etree.tostring(node_copy, pretty_print=False)).split(' ')
        isGMLTag = lambda tag : not tag.startswith('xmlns') or tag.startswith('xmlns:gml')
        geometry = ' '.join(filter( isGMLTag, geometry ))
        geometry = str(geometry)[2:-1].replace('\\n', '').replace('  ', '').replace('"', "'").strip().replace(
            "xmlns:gml='http://www.opengis.net/gml/3.2'", "xmlns:gml='http://www.opengis.net/gml'")
        # if atomic_geometry is not enabled add the descendant gml nodes to the parsed_nodes list
        # so they will not be converted to RDF.
        if not self.args.atomic_geometry:
            for descendant in node.iter():
                self.parsed_nodes.append(self.input_tree.getelementpath(descendant))
                self.updateProgressBar(descendant.tag)
        return geometry



    #########################
    ##  Utility functions  ##
    #########################

    def uriToLXML(uri):
        '''convert a uri string to an lxml friendly string'''
        if '#' in uri:
            uri = uri.split('#')
            return '{%s#}%s' % (uri[0], uri[1])
        else:
            uri = uri.split('/')
            return '{%s/}%s' % ('/'.join(uri[0:-1]), uri[-1])


    def normalizeNamespace(namespace):
        '''normalize an namespace for OWL'''
        namespace = str(namespace)
        if namespace[-1] == '#':
            return namespace
        elif namespace[-1] == '/':
            return namespace[:-1] + '#'
        else:
            return namespace + '#'


    def mapNamespace(self, node_or_tag):
        '''map an XML tag namespace based based on the given mapping file. If an RDF
        mapping exists for the tag, simply return that mapping. If a tag namespace is
        in namespace mappings, return the namespace mapping. When a tag namespace is
        mapped to multiple namespaces, the ontology model is queried to determine which
        namespace is appropriate. The first target namespace+localname to appear in
        the ontology model, is selected as the target namespace. Namespaces are returned
        as rdflib.URIRef objects.'''
        if node_or_tag in self.rdf_mappings:
            # if an rdf mapping exists, convert it into a lxml friendly format
            return URIRef( self.rdf_mappings[node_or_tag] )
            # mapped_tag = self.rdf_mappings[node_or_tag]
            # if '#' in mapped_tag:
            #     mapped_tag = mapped_tag.split('#')
            #     return URIRef( '{%s#}%s' % (mapped_tag[0], mapped_tag[1]) )

        qname = etree.QName(node_or_tag)

        if qname.namespace in self.namespace_mappings.keys():
            if len(self.namespace_mappings[qname.namespace]) == 1:
                return URIRef(self.namespace_mappings[qname.namespace][0] + qname.localname)
            else:
                # TODO: implement dynamic namespace resolution for multiple mappings
                for namespace in self.namespace_mappings[qname.namespace]:
                    if self.ontology.query('''
                            ASK { <%s%s> ?predicate ?object }''' % (namespace, qname.localname)):
                        return URIRef(namespace + qname.localname)
                logging.warning(f'Unable to map qname, {qname}, to a namespace')
                return URIRef(self.normalizeNamespace(qname.namespace) + qname.localname)
        else:
            return URIRef(self.normalizeNamespace(qname.namespace) + qname.localname)


    def generateID(self, tag):
        '''create a new, unique id from a normalized XML node tag'''
        name = str(tag).split('#')[-1]
        if name in self.id_count:
            self.id_count[name] += 1
            return f'{self.output_uri}#{name}_{self.id_count[name]}'
        else:
            self.id_count[name] = 0
            return f'{self.output_uri}#{name}_0'



    #########################
    ##  Query functions  ##
    #########################

    def findObjectProperty(self, tag1, tag2=None, property_tag=None):
        '''Find an object property which links (intersects) two given classes based on the
        domain and range of the property. The second class can be omitted in the case of
        an xlink object property. A property tag hint may also be supplied.'''
        #TODO: Break this function into multiple functions
        qname1 = etree.QName(tag1)
        if tag2 is None and self.isClass(qname1):
            if property_tag is None:
                query = self.ontology.query('''
                    SELECT DISTINCT ?objectproperty
                    WHERE {
                        {   ?objectproperty a owl:ObjectProperty ;
                                rdfs:domain ?domain .
                            <%s> rdfs:subClassOf* ?domain .
                        }
                        UNION
                        {   <%s> a owl:Class ;
                                rdfs:subClassOf [ a owl:Restriction ;
                                                owl:allValuesFrom ?someClass ;
                                                owl:onProperty    ?objectproperty 
                                                ] .
                        }
                    }''' % (self.mapNamespace(qname1),
                            self.mapNamespace(qname1)))
                if len(query) > 0:
                    return [line[0] for line in query]
            else:
                for property in self.getObjectProperties(property_tag):
                    query = self.ontology.query('''
                        ASK   {
                            { <%s> a owl:ObjectProperty ;
                                    rdfs:domain ?domain .
                                <%s> rdfs:subClassOf* ?domain .
                            }
                            UNION
                            { <%s> a owl:Class ;
                                rdfs:subClassOf [ a owl:Restriction ;
                                                owl:allValuesFrom ?someClass ;
                                                owl:onProperty    <%s> 
                                                ] .
                            }
                        }''' % (property[0],
                                self.mapNamespace(qname1),
                                self.mapNamespace(qname1),
                                property[0]) )
                    if bool(query):
                        return property[0]

        qname2 = etree.QName(tag2)
        if not self.isClass(qname1) or not self.isClass(qname2):
            logging.warning(f'Cannot find object property between {tag1} or {tag2} '
                f'for property {property_tag}. One or both are not classes')
            return None

        if property_tag is None:
            query = self.ontology.query('''
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
                }''' % (self.mapNamespace(qname1),
                        self.mapNamespace(qname2),
                        self.mapNamespace(qname1),
                        self.mapNamespace(qname2)))
            if len(query) > 0:
                return [line[0] for line in query]
        else:
            for property in self.getObjectProperties(property_tag):
                query = self.ontology.query('''
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
                            self.mapNamespace(qname1),
                            self.mapNamespace(qname2),
                            self.mapNamespace(qname1),
                            self.mapNamespace(qname2),
                            property[0]) )
                if bool(query):
                    return property[0]
        logging.warning(f'No matching object property found between: {tag1}, {tag2}')
        return None


    def findDatatypeProperty(self, tag, property_tag=None):
        '''Find a datatype property which links (intersects) a given class and a datatype
        based on the domain and range of the property or which a given class contains
        a universal restriction of the property.'''
        qname1 = etree.QName(tag)
        if self.isClass(tag):
            if property_tag is None:
                query = self.ontology.query('''
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
                    }''' % (self.mapNamespace(qname1),
                            self.mapNamespace(qname1)) )
                if len(query) > 0:
                    return [line[0] for line in query]
            else:
                for property in self.getDatatypeProperties(property_tag):
                    query = self.ontology.query('''
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
                                self.mapNamespace(qname1),
                                self.mapNamespace(qname1),
                                property[0]) )
                    if bool(query):
                        return property[0]
        logging.warning(f'No matching datatype property found between: {tag}, {property_tag}')
        return None


    def findDatatypeProperty(self, tag, property_tag=None):
        '''Find a datatype property which links (intersects) a given class and a datatype
        based on the domain and range of the property or which a given class contains
        a universal restriction of the property.'''
        qname1 = etree.QName(tag)
        if self.isClass(tag):
            if property_tag is None:
                query = self.ontology.query('''
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
                    }''' % (self.mapNamespace(qname1),
                            self.mapNamespace(qname1)) )
                if len(query) > 0:
                    return [line[0] for line in query]
            else:
                for property in self.getDatatypeProperties(property_tag):
                    query = self.ontology.query('''
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
                                self.mapNamespace(qname1),
                                self.mapNamespace(qname1),
                                property[0]) )
                    if bool(query):
                        return property[0]
        logging.warning(f'No matching datatype property found between: {tag}, {property_tag}')
        return None


    def isClass(self, tag):
        '''return whether class definition exists in ontology'''
        qname = etree.QName(tag)
        if tag in self.class_definition_cache.keys():
            return len(self.class_definition_cache.get(tag)) > 0
        # TODO: optimize query
        query = []
        tag_namespace_mappings = self.namespace_mappings.get(qname.namespace)
        if tag_namespace_mappings is None:
            for line in self.ontology.query('''
                            SELECT DISTINCT ?class
                            WHERE {
                                ?class rdf:type owl:Class .
                                FILTER ( STR(?class) = "%s%s" )
                            }''' % (qname.namespace, qname.localname)):
                query.append(line)
        else:
            for namespace in self.namespace_mappings.get(qname.namespace, [qname.namespace]):
                for line in self.ontology.query('''
                        SELECT DISTINCT ?class
                        WHERE {
                            ?class rdf:type owl:Class .
                            FILTER ( STR(?class) = "%s%s" )
                        }''' % (namespace, qname.localname) ):
                    query.append(line)
        self.class_definition_cache[tag] = query
        return len(self.class_definition_cache.get(tag)) > 0


    def getClasses(self, tag):
        '''check if uri corresponds to a class and return the possible classes'''
        if self.isClass(tag):
            return self.class_definition_cache.get( str(tag) )
        else:
            logging.warning(f'Unable to get Class: {tag}')


    def isObjectProperty(self, tag, parent_tag=None):
        '''return whether object property definition exists in self.ontology. Local property
        names may require searching through the shapechange [[name]] descriptor target
        (rdfs:label by default) depending on shapechange property encoding
        configurations. The node's parent tag may be provided to verify if the parent
        class is within the domain of the property.'''
        qname = etree.QName(tag)
        if tag in self.objectproperty_definition_cache.keys():
            if parent_tag is None:
                return len(self.objectproperty_definition_cache.get(tag)) > 0
            else:
                parent_qname = self.mapNamespace(etree.QName(parent_tag))
                for objectproperty in self.objectproperty_definition_cache.get(tag):
                    if self.ontology.query('''
                        ASK {
                            <%s> rdf:type owl:ObjectProperty ;
                                rdfs:domain ?domain .
                            <%s> rdfs:subClassOf* ?domain .
                        }''' % (objectproperty[0], parent_qname) ):
                        return True
                return False
        query = []
        tag_namespace_mappings = self.namespace_mappings.get(qname.namespace)
        if tag_namespace_mappings is None:
            for line in self.ontology.query('''
                    SELECT DISTINCT ?objectproperty
                    WHERE {
                        ?objectproperty rdf:type owl:ObjectProperty .
                        FILTER regex(STR(?objectproperty), "^%s.*.%s$")
                    }''' % (qname.namespace, qname.localname) ):
                query.append(line)
        else:
            for namespace in self.namespace_mappings[qname.namespace]:
                # TODO: optimize queries
                for line in self.ontology.query('''
                        SELECT DISTINCT ?objectproperty
                        WHERE {
                            ?objectproperty rdf:type owl:ObjectProperty .
                            FILTER regex(STR(?objectproperty), "^%s.*.%s$")
                        }''' % (namespace, qname.localname) ):
                    query.append(line)
        self.objectproperty_definition_cache[tag] = query
        if parent_tag is None:
            return len(self.objectproperty_definition_cache.get(tag)) > 0
        else:
            parent_qname = self.mapNamespace(etree.QName(parent_tag))
            for objectproperty in self.objectproperty_definition_cache.get(tag):
                if self.ontology.query('''
                    ASK {
                        <%s> rdf:type owl:ObjectProperty ;
                            rdfs:domain ?domain .
                        <%s> rdfs:subClassOf* ?domain .
                    }''' % (objectproperty[0], parent_qname)):
                    return True
        return False



    def getObjectProperties(self, tag):
        '''check if uri corresponds to an object property and return the possible properties'''
        if self.isObjectProperty(tag):
            return self.objectproperty_definition_cache.get( str(tag) )
        else:
            logging.warning(f'Unable to get Object Property: {tag}')


    def isDatatypeProperty(self, tag, parent_tag=None):
        '''return whether datatype property definition exists in self.ontology. Local property
        names may require searching through the shapechange [[name]] descriptor target
        (rdfs:label by default) depending on shapechange property encoding
        configurations. The node's parent tag may be provided to verify if the parent
        class is within the domain of the property.'''
        qname = etree.QName(tag)
        if tag in self.datatypeproperty_definition_cache.keys():
            if parent_tag is None:
                return len(self.datatypeproperty_definition_cache.get(tag)) > 0
            else:
                parent_qname = self.mapNamespace(etree.QName(parent_tag))
                for datatypeproperty in self.datatypeproperty_definition_cache.get(tag):
                    if self.ontology.query('''
                            ASK {
                                <%s> rdf:type owl:DatatypeProperty ;
                                    rdfs:domain ?domain .
                                <%s> rdfs:subClassOf* ?domain .
                            }''' % (datatypeproperty[0], parent_qname) ):
                        return True
                return False
        query = []
        tag_namespace_mappings = self.namespace_mappings.get(qname.namespace)
        if tag_namespace_mappings is None:
            for line in self.ontology.query('''
                        SELECT DISTINCT ?datatypeproperty
                        WHERE {
                            ?datatypeproperty rdf:type owl:DatatypeProperty .
                            FILTER regex(STR(?datatypeproperty), "^%s.*.%s$")
                        }''' % (qname.namespace, qname.localname) ):
                    query.append(line)
        else:
            for namespace in tag_namespace_mappings:
                # TODO: optimize query
                for line in self.ontology.query('''
                        SELECT DISTINCT ?datatypeproperty
                        WHERE {
                            ?datatypeproperty rdf:type owl:DatatypeProperty .
                            FILTER regex(STR(?datatypeproperty), "^%s.*.%s$")
                        }''' % (namespace, qname.localname) ):
                    query.append(line)
        self.datatypeproperty_definition_cache[tag] = query
        if parent_tag is None:
            return len(self.datatypeproperty_definition_cache.get(tag)) > 0
        else:
            parent_qname = self.mapNamespace(etree.QName(parent_tag))
            for datatypeproperty in self.datatypeproperty_definition_cache.get(tag):
                if self.ontology.query('''
                    ASK {
                        <%s> rdf:type owl:DatatypeProperty ;
                            rdfs:domain ?domain .
                        <%s> rdfs:subClassOf* ?domain .
                    }''' % (datatypeproperty[0], parent_qname)):
                    return True
        return False


    def isAnnotationProperty(self, tag):
        '''return whether an annotation property definition exists in the self.ontology.
        Local naming conventions are not used for this query.'''
        # TODO: add annotation definition cache
        qname = etree.QName(tag)
        query = []
        tag_namespace_mappings = self.namespace_mappings.get(qname.namespace)
        if tag_namespace_mappings is None:
            for line in self.ontology.query('''
                        SELECT DISTINCT ?annotationproperty
                        WHERE {
                            ?annotationproperty rdf:type owl:AnnotationProperty .
                            FILTER regex(STR(?annotationproperty), "^%s%s")
                        }''' % (qname.namespace, qname.localname) ):
                query.append(line)
        else:
            for namespace in tag_namespace_mappings:
                # TODO: optimize query
                for line in self.ontology.query('''
                        SELECT DISTINCT ?annotationproperty
                        WHERE {
                            ?annotationproperty rdf:type owl:AnnotationProperty .
                            FILTER regex(STR(?annotationproperty), "^%s%s")
                        }''' % (namespace, qname.localname) ):
                    query.append(line)
        return len(query) > 0


    def getDatatypeProperties(self, tag):
        '''check if uri corresponds to an datatype property and return the possible properties'''
        if self.isDatatypeProperty(tag):
            return self.datatypeproperty_definition_cache.get( str(tag) )
        else:
            logging.warning(f'Unable to get Datatype Property: {tag}')


    def getDatatypePropertyRange(self, property):
        '''Get the rdfs:range of a datatype property'''
        return self.ontology.query('''
            SELECT DISTINCT ?range
            WHERE {
                <%s> rdfs:range ?range .
            }''' % property)


    def isDatatype(self, tag):
        '''return whether datatype definition exists in ontology'''
        qname = etree.QName(tag)
        if tag in self.datatype_definition_cache.keys():
            return len(self.datatype_definition_cache.get(tag)) > 0
        # TODO: optimize query
        query = []
        tag_namespace_mappings = self.namespace_mappings.get(qname.namespace)
        if tag_namespace_mappings is None:
            for line in self.ontology.query('''
                    SELECT DISTINCT ?datatype
                    WHERE {
                        ?datatype rdf:type rdfs:Datatype .
                        FILTER ( STR(?datatype) = "%s%s" )
                    }''' % (qname.namespace, qname.localname) ):
                query.append(line)
        else:
            for namespace in self.namespace_mappings[qname.namespace]:
                for line in self.ontology.query('''
                        SELECT DISTINCT ?datatype
                        WHERE {
                            ?datatype rdf:type rdfs:Datatype .
                            FILTER ( STR(?datatype) = "%s%s" )
                        }''' % (namespace, qname.localname) ):
                    query.append(line)
        self.datatype_definition_cache[tag] = query
        return len(self.datatype_definition_cache.get(tag)) > 0


    def getDatatype(self, tag):
        '''check if uri corresponds to a datatype and return the possible datatypes'''
        if self.isDatatype(tag):
            return self.datatype_definition_cache.get( str(tag) )
        else:
            logging.warning(f'Unable to get Datatype: {tag}')


    def isGeometry(self, tag):
        '''return whether tag is a valid gml element supported by Parliament. The uri is
        converted into a qname tuple of (prefix, namespace, localname) to extract the
        namespace. Note that solid geometry is not supported by Parliament but multisurface
        geometry is.'''
        qname = self.mapNamespace(tag).split('#')
        if qname[0] + '#' == str(self.GML_ONT_NAMESPACE) and self.isClass(tag):
            if self.valid_geometry:
                return qname[1] in self.valid_geometry
            else:
                return self.ontology.query(
                    'ASK {'
                        f'<{str(self.GML_ONT_NAMESPACE)}{qname[1]}> rdfs:subClassOf* <{str(self.GML_ONT_NAMESPACE)}AbstractGeometry> .'
                    '}')

    def updateProgressBar(self, status=''):
        if self.args.verbose:
            bar_length    = 20
            buffer_size   = 127
            ratio = self.input_node_count / float(self.input_node_total)
            filled_length = int(round(bar_length * ratio))

            percent = round(100.0 * self.input_node_count / float(self.input_node_total), 1)
            bar = '#' * filled_length + '-' * (bar_length - filled_length)
            output = f'[{bar}] {percent}%, {self.input_node_count}/{self.input_node_total} ...{status}'

            print(' ' * buffer_size + '\r', end='')
            print(output[0:buffer_size] + '\r', end='')
        self.input_node_count += 1



if __name__ == "__main__":
    main()