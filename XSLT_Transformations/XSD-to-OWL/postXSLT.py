import sys
import re
from lxml import etree

if len(sys.argv) != 3:
   sys.exit('Incorrect number of arguments. Usage: qualify-ns.py [xsd with namespaces] [rdf to clean]')

# Initialize variables
filename = sys.argv[1].split('/')[-1]
root     = etree.parse(sys.argv[1]).getroot()

# Get namespaces
namespaces = root.nsmap
namespaces.update({'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns'})
namespaces.update({'rdfs': 'http://www.w3.org/2000/01/rdf-schema'})
namespaces.pop(None, None) # remove nil namespace prefixes

# Iterate through file line by line
new_file_content = ''
with open(sys.argv[2]) as file:
   line = file.readline()
   while line != '':
      # update ontology uri to reference given filename
      line = line.replace( '<owl:Ontology rdf:about="">',
                           '<owl:Ontology rdf:about="{}">'.format( 'http://liris.cnrs.fr/ontologies/' + filename.split('.')[0] ))

      # update import statements with local naming conventions ontologies
      if re.match( '<owl:imports rdf:resource=".*?"/>', line.strip() ) != None:
         split_line = line.split('"')
         line = ( split_line[0] + '"http://liris.cnrs.fr/ontologies/{}"'.format( split_line[1].split('/')[-1].split('.')[0] ) +
            split_line[2] )

      # fully qualify namespace
      for prefix in namespaces.keys():
         line = line.replace( 'rdf:resource="{}:'.format(prefix), 'rdf:resource="{}#'.format(namespaces[prefix]) )
         line = line.replace( 'rdf:datatype="{}:'.format(prefix), 'rdf:datatype="{}#'.format(namespaces[prefix]) )
         line = line.replace( 'rdf:type="{}:'.format(prefix),     'rdf:type="{}#'.format(namespaces[prefix]) )
         line = line.replace( 'rdf:about="{}:'.format(prefix),    'rdf:about="{}#'.format(namespaces[prefix]) )
      new_file_content += line
      line = file.readline()

with open(sys.argv[2], 'w') as file:
   file.write(new_file_content)

# sys.stdout.write('Namespaces Qualified: {}\r'.format(namespaces.keys()))
# sys.stdout.flush()