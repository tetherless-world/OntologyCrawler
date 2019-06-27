#!/usr/bin/env python3

'''
File: pipeline.py
Author: Spencer Norris

Description: This pipeline will recursively retrieve class
hierarchies for a collection of classes, crawling ontology
import statements to expand the class hierarchy as far as
possible.

First, ontologies are recursively pulled in, using the base
ontology as the root node. owl:imports statements are crawled
and each ontology is parsed into a separate graph. This graph
is then queried for owl:imports statements and added to the
global graph, and so forth until there are no more owl:imports
statements to be read.

Next, the graph is queried to retrieve class hierarchies from the imports.
Given that all classes inherit from owl:Thing, the recursion
across the class hierarchies should theoretically result in
a directed acyclic graph with one sink node (owl:Thing). However,
since it is possible that cycles can be formed between class
hierarchies, the 'seen' set is used to track nodes in the
graph that have already been traversed, preventing infinite recursion.

The recursive class retrieval is replicated for BioPortal, less the
local ontology imports; BioPortal is simply too big to pull into
memory. Each class is expanded for paths using the PREDICATES 
provided. The relation (predicate) between the input class (subject) 
and the connected class (object) is then added to a local graph.
'''
#TODO: Retrofit so that a SPARQL endpoint, such as Blazegraph,
# can be used to store results.

from rdflib import Graph, URIRef
from rdflib.namespace import RDFS, OWL
from SPARQLWrapper import SPARQLWrapper, JSON, XML, N3, RDF

from copy import deepcopy
import sys
import os

########## Globals ###############################################


#Parameters for guiding BioPortal class retrieval
seen = set() #Classes that we've already expanded
bioportal_graph = Graph() #Where we'll expand the BioPortal results
BIOPORTAL_API_KEY = os.environ['BIOPORTAL_API_KEY']
bioportal = SPARQLWrapper('http://sparql.bioontology.org/sparql/')
bioportal.addCustomParameter("apikey", BIOPORTAL_API_KEY)




########## Reporting Methods #############################################################################
'''
These are only used to display information about our results. 
They can be safely removed from the code base without any 
side effects, provided that their calls are removed from main().
'''

def show_ontologies(graph):
	'''
	Print list of all ontologies now present in Graph.
	'''
	all_res = graph.query("""
		PREFIX owl: <http://www.w3.org/2002/07/owl#>
		SELECT ?ont WHERE {
			?ont a owl:Ontology.
		}
		""")
	import_res = graph.query("""
		PREFIX owl: <http://www.w3.org/2002/07/owl#>
		SELECT ?ont WHERE {
			?ont a owl:Ontology.
			[] owl:imports ?ont .
		}
		""")
	print("All ontologies: ")
	for r in all_res:
		print(str(r[0]))
	print(len(all_res), " total.")


# def report_bioportal():
# 	global seen
# 	global bioportal_graph
# 	#TODO: Expand with more information on 
# 	print("Number of BioPortal superclasses: ", len(seen))


########## BioPortal Graph Crawling ####################################################################

#Adapted from https://github.com/ncbo/sparql-code-examples/blob/master/python/sparql1.py

import json
import urllib
from urllib.parse import urlencode, quote_plus
import traceback


def find_bioportal_superclasses(k,i):
	'''
	This is a recursive method that will move all
	the way up the inheritance tree until we hit 
	superclass bedrock for the class we've been given.
	The recursion should follow the path of a directed
	acyclic graph, with one sink node, owl:Thing.

	It's possible that we wind up with cycles; in order
	to deal with this, we're going to maintain a set
	containing all of our classes which we've already
	expanded, called 'klasses'.

	params:
	k --> our class that we want to expand
	klasses --> set of classes already expanded.
	'''
	global seen
	global bioportal_graph
	global PREDICATES

	def _query_next_level(k):
		'''
		Retrieve the next level up of the predicate path
		using the class k.
		'''
		global PREDICATES
		global bioportal

		#Construct filter so that we only retrieve predicates we're interested in 
		filter_str = "FILTER(" + " || ".join(["?pred = <%s>" % (pred,) for pred in PREDICATES]) + ")"
		query = """
		SELECT DISTINCT ?pred ?kn WHERE {
			<%s> ?pred ?kn.
			%s
		}
		""" % (str(k),filter_str)
		bioportal.setQuery(query)
		bioportal.setReturnFormat(JSON)
		results = bioportal.query().convert()

		#Construct dictionary mapping superclasses to lists of connecting properties
		final = {}
		for result in results['results']['bindings']:
			if not result['kn']['value'] in final.keys():
				final[URIRef(result['kn']['value'])] = []
			final[URIRef(result['kn']['value'])].append(URIRef(result['pred']['value']))
		return final

	print("Recursion level: ", i)
	print("Node: ", str(k))

	#If we've already expanded this node, don't recurse
	if str(k) in seen:
		print("Already seen!")
		return
	else:				
		#Note that we're about to expand the parent
		print("Not seen, expanding...")
		seen.add(str(k))

	#Retrieve all parents, properties for connecting back to input class
	parents = _query_next_level(k)
	#Go over all of the classes that were retrieved, if any
	for k_n in parents.keys():
		#Add property connections to graph
		for pred in parents[k_n]:
			bioportal_graph.add((k,pred,k_n))
		#Expand our next node
		find_bioportal_superclasses(k_n,i+1)


def find_bioportal_subclasses(k):
	global bioportal
	global bioportal_graph
	global PREDICATES

	#Construct query with filter to select only predicates we're interested in
	filter_str = "FILTER(" + " || ".join(["?pred = <%s>" % (pred,) for pred in PREDICATES]) + ")"
	query = """
		PREFIX owl: <http://www.w3.org/2002/07/owl#>
		PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
		SELECT ?pred ?sub WHERE {
			?sub ?pred <%s>.
			%s
		}
		""" % (str(k),filter_str)
	res = bioportal.setQuery(query)
	bioportal.setReturnFormat(JSON)
	results = bioportal.query().convert()
	#Dump results into BioPortal graph 
	for result in results['results']['bindings']:
		bioportal_graph.add( (URIRef(result['sub']['value']), URIRef(result['pred']['value']), k) )


########## Graph Crawling ####################################################################

def retrieve_ontologies(graph, error=None, inplace=True):
	'''
	This method will recursively crawl owl:import statements,
	starting with any statements present in graph.

	'error' defines what to do in the event of the inability
	to parse an ontology. If error=None, then the method will
	fail quickly and raise an Exception. If error='ignore',
	then the ontology will simply be ignored, as if it weren't
	the object of an owl:imports call.

	'inplace' determines what the method will return. If inplace=True,
	then the input graph will be included alongside all imported ontologies.
	If not, a graph containing only our imported ontologies will be returned.
	'''
	gout = Graph()
	seen = set()
	def _import_ontologies(g):
		'''
		Recursively work over ontology imports,
		querying for new import statements and
		adding the read data to the global graph.
		'''
		nonlocal gout
		nonlocal seen
		nonlocal error

		#Retrieve all ontology imports
		imports = g.query("""
			PREFIX owl: <http://www.w3.org/2002/07/owl#>
			SELECT ?ont WHERE{
			[] a owl:Ontology ; 
			   owl:imports ?ont .
			}
			""")

		#Attempt to read in ontologies
		for row in imports:
			#Check whether we've already imported
			if row[0] in seen:
				continue
			else:
				seen.add(row[0])

			#Import the ontology
			FORMATS=['xml','n3','nt','trix','rdfa']
			read_success = False
			for form in FORMATS:
				try:
					#If we successfully read our ontology, recurse
					gin = Graph().parse(str(row[0]),format=form)
					#Add g to our graph
					gout = gout + gin
					read_success = True
					_import_ontologies(gin)
					break
				except Exception as e:
					pass

			#If unable to parse ontology, decide how to handle error
			if not read_success:
				if error is None:
					raise Exception("Exhausted format list. Failing quickly.")
				if error == 'ignore':
					print("Exhausted format list. Quietly ignoring failure.")
				
	_import_ontologies(graph)

	#Return a graph containing our input graph and imports
	if inplace:
		return graph + gout
	#Return a graph containing just the imports
	else:
		return gout


def extract_paths(seed, graph, properties,verbose=False,down=True,up=True,shallow=None,up_shallow=True,down_shallow=True):
	#TODO: Modify this so it returns a graph with all of the classes
	# and instances in the property path without the ontologies.
	'''
	We're going to pull in all of the classes that are
	accessible via a local import. That means any classes
	that are present in CHEAR, any of the ontologies it
	imports, and any ontologies further up that chain.
	'''

	if shallow is not None:
		up_shallow = shallow
		down_shallow = shallow

	#TODO: Modify SPARQL queries so that our list of predicates are used instead.
	def __find_subclasses():
		'''
		We're only interested in finding depth-one subclasses.
		Shouldn't be too difficult
		'''
		nonlocal graph
		nonlocal seed
		nonlocal down_shallow
		res = graph.query("""
			PREFIX owl: <http://www.w3.org/2002/07/owl#>
			PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
			SELECT ?sub WHERE {
				?sub (rdfs:subClassOf|owl:equivalentClass)%s <%s>.
			}
			""" % ("" if down_shallow else "+", str(seed)))
		return {r[0] for r in res}

	def __find_superclasses():
		#Retrieve superclass hierarchy for all seed classes
		nonlocal graph
		nonlocal seed
		nonlocal up_shallow
		superclasses = set()
		res = graph.query("""
			PREFIX owl: <http://www.w3.org/2002/07/owl#>
			PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
			SELECT ?class WHERE{
				<%s> (rdfs:subClassOf|owl:equivalentClass)%s ?class.
			}
			""" % (str(seed), "" if up_shallow else "+"))
		for r in res:
			superclasses.add(r[0])
		return superclasses

	gout = Graph()

	#Retrieve our subclasses
	if up:
		superclasses = set(__find_superclasses())
	if down:
		subclasses = set(__find_subclasses())

	if verbose:
		print("Full hierarchy size: ", len(set.union(seed, superclasses)))
		print("Intersection of seed classes and extracted hierarchy: ", 
			len(set.intersection(set(seed), superclasses))
		)
		print("Number of non-seed hierarchy classes: ",
			len(superclasses) - len(set.intersection(seed, superclasses))
		)
		print("Total immediate subclasses: ", len(subclasses))

	return superclasses, subclasses


def _retrieve_seed_classes(graph,query):
	'''
	TODO: Implement some validation to make sure
	that our query only retrieves one variable.
	'''
	return {row[0] for row in graph.query(query)}


def retrieve_crawl_paths(graph,seed_query,properties,
	expand_ontologies=True,verbose=False,inplace=False,
	extract_params={'up' : True, 'down' : True, 'up_shallow' : False, 'down_shallow' : False}):
	'''
	Method for retrieving entity paths for the given
	list of properties from the input graph, without including full ontology imports.

	seed_query is a SPARQL query designating what classes from graph 
	to use as root nodes when expanding the property paths.

	Property paths is a list of properties to follow through the graph.
	Note that all properties will be used at every level of recursion.
	For example, assume our properties are [P1,P2]. Say that A connects to B with P1,
	and that B connects to C with P2. In this case, the full path will be retrieved:

	<A> <P1> <B>.
	<B> <P2> <C>.

	If the user only wants the one property along a given path, then they should call
	this method twice: once for P1, and one for P2.

	retrieve_paths(<P1>) --> <A> <P1> <B> .
	retrieve_paths(<P2>) --> <B> <P2> <C> .

	This method is configurable, with different flags which
	will adjust its behavior:

		- expand_ontologies: whether or not to recursively pull ontologies
			referenced via owl:imports. This will recurse until
			no more ontologies can be imported.

		- inplace: If True, return a graph containing the input graph and retrieved data.
			Otherwise, return a graph containing only the retrieved property paths.


	The real value of this method is being able to walk property paths across multiple
	ontologies without needing to keep them, e.g. expand_ontologies=True. Otherwise,
	the property paths will only be retrieved for the input graph.

	If the user wishes to add the full ontology tree to their graph, including all of
	the property paths, they can instead call graph = retrieve_ontologies(graph,inplace=True) .
	'''
	#TODO: We want this to return a graph under a few different conditions:
	#  We might want to return the original graph with ontologies included
	#  We might want to return the original graph with just retrieved instances included
	#  We might want to return a separate graph with just retrieved instances
	#  We might want to return a separate graph with full ontologies loaded

	#Collect the initial seed of classes to expand
	seed = _retrieve_seed_classes(graph,seed_query)
	if verbose:
		print("Number of seed classes: ", len(seed))
		print("Sample classes: ")
		for i in range(10):
			print(list(seed)[i])

	#Decide whether to pull in ontologies
	if expand_ontologies:
		ontology_graph = retrieve_ontologies(graph,inplace=False)
		if verbose:
			show_ontologies(graph + ontology_graph)
	else:
		ontology_graph = Graph()

	#TODO: Temporary None keyword, replace with predicates
	#TODO: add verbose information regarding retrieved entities
	#Pull any property paths
	entity_graph = Graph()
	for s in seed:
		entity_graph += extract_paths(s, graph + ontology_graph, None, **extract_params)

	#Decide whether or not to lump everything into original graph
	if inplace:
		return graph + entity_graph
	else:
		return entity_graph


def bioportal_expand_paths(graph,seed_query):
	global bioportal_graph
	seed = _retrieve_seed_classes(seed_query)

	#Pull in BioPortal hierarchies
	print("Seeding BioPortal superclasses.")
	counter = 0
	for s in seed:
		print("=============== Class ", counter, " being expanded.")
		find_bioportal_superclasses(s,0)
		find_bioportal_subclasses(s)
		counter += 1
	print("BioPortal superclasses retrieved.")
	bio_super, bio_sub = extract_paths(seed, bioportal_graph, None, verbose=verbose, **extract_params)


############### Main Section ####################################################################


def expand(base_url, other_url):
	pass

if __name__ == '__main__':
	#Predicates we're interested in expanding paths for
	PREDICATES = [ #predicates we'll recursively expand paths for 
		RDFS.subClassOf,
		OWL.equivalentClass
	]
	CHEAR_LOCATION="/home/s/projects/TWC/chear-ontology/chear.ttl"
	## This seed query will select all ChEBI classes present in CHEAR.
	seed_query = """
		PREFIX owl: <http://www.w3.org/2002/07/owl#>
		SELECT DISTINCT ?c WHERE{
			?c a owl:Class .
			FILTER(regex(str(?c), "http://purl.obolibrary.org/obo/CHEBI"))
		}
		"""
	graph = Graph()
	graph.parse(CHEAR_LOCATION,format='turtle')
	# graph = retrieve_ontologies(graph, inplace=False)
	extract_params = {
		'up' : True, 
		'down' : True, 
		'up_shallow' : False, 
		'down_shallow' : True, 
		'verbose' : False
	}
	retrieve_crawl_paths(graph, seed_query, PREDICATES, verbose=True, inplace=True)