#!/usr/bin/python
# metagene_maker.py
# 8/29/14
# makes metagenes for bedgraphs and regions according to a configuration file

# SNF TODO 
# - does not handle short (especially 1 nt long) regions - dies inside Rscript.  Use some cutoff value to trim super short things?  These may distort analysis 
# - to this point, report a histogram of region sizes for processed regions in region space (not chr space) 
# - parse blocks for multi exon regions in the input bed file and turn these into a new object that has a method that can map bin space onto chr space and vice versa 


import os, glob, csv, re, collections, math, multiprocessing, sys, random
from binning_functions import *
from merge_bins import *
csv.register_dialect("textdialect", delimiter='\t')

def readConfigFile(fn):
	# Input: a configuration file with parameters, folders, and regions
	# Output: these parameters in a hashmap format

	ifile = open(fn, 'r')
	reader = csv.reader(ifile, 'textdialect')

	# params
	config = {}
	for row in reader:
		if len(row)==0: continue
		if 'folder' in row[0]: break
		if '#' in row[0] or row[0]=='': continue
		config[row[0]] = row[1]

	# folders
	folders = {}
	for row in reader:
		if len(row)==0: continue
		if 'regionType' in row[0]: break
		if '#' in row[0] or row[0]=='': continue
		folders[row[0]] = row[1:]

	# regions
	regions = {}
	for row in reader:
		if len(row)==0: continue
		if '#' in row[0] or row[0]=='': continue
		regions[row[0]] = row[1:]

	return config, folders, regions

def processFolders(parentDir, folders, regions):
	folderToGraph = {}
	for folder in folders:

		# setting up folders
		os.chdir(parentDir)
		if not glob.glob(folder + '/'): os.mkdir(folder)
		os.chdir(folder)
		
		if not glob.glob('bins/'): os.system("mkdir bins")
		os.chdir('bins')
		#os.system("rmdir *") # removing empty directories
		regionFolders = ' '.join(regions.keys())
		os.system("mkdir " + regionFolders)

		# splitting up bedgraph if not done already
		os.chdir("..")
		if not glob.glob("bedGraphByChr/"): os.system("mkdir bedGraphByChr")
		
		os.chdir("bedGraphByChr")
		if not glob.glob("*.bedGraph"):
			os.system("rm -f *.bedGraph")
			print "Splitting up bedgraph for " + folder
			#SNF mod: awk -> gawk 
			cmd = "gawk '{print >> $1\".bedGraph\"}' " + folders[folder][0]
			print cmd
			os.system(cmd)

		# making folder to bedgraph relationship
		binFolder = parentDir + '/' + folder + '/bins/'
		graphFolder = parentDir + '/' + folder + '/bedGraphByChr/'
		folderToGraph[folder] = [binFolder, graphFolder, folders[folder][1]] #bin folder --> [graph folder, strand]

	return folderToGraph

def getChrToRegion(fn, chrCol, header):
	#SNF mod to with clause ifile = open(fn, 'r')
	with open(fn, 'r') as ifile:
           #reader = csv.reader(ifile, 'textdialect', delimiter=' ')  # SNF not whitespace-safe 
	
		regions = collections.defaultdict(lambda: []) # by chromosome
	        #SNF mod if header: reader.next()
		if header: ifile.next()
	        #SNF mod for row in reader:
		for line in ifile:
			row = line.split() # added by SNF 
			regions[row[chrCol]].append(row)
	#SNF mod ifile.close()
	return regions

def processRegions(regions):
	regionToChrMap = {}
	for region in regions:
		info = regions[region]
		loc = info[0]
		chrCol = int(info[2])
		isHeader = True if info[1] == 'y' else False
		regionToChrMap[region] = getChrToRegion(loc, chrCol, isHeader)

	return regionToChrMap

def main():
	# check
	if len(sys.argv) < 2: 
		print "Need configuration file."
		exit()

	# log file
	# logfile = open('logs/' + str(random.randrange(1,1000)) + '.log', 'w')
	# logwriter = csv.writer(logfile, 'textdialect')
	
	# reading config file
	config, folders, regions = readConfigFile(sys.argv[1])
	print "Read configuration file"
	print config, folders, regions

	# processing folders and bedgraphs
	parentDir = config["parentDir"]
	folderToGraph = processFolders(parentDir, folders, regions)
	print "Processed folders:", ', '.join(folderToGraph.keys())
	print folderToGraph
	
	# processing regions
	regionToChrMap = processRegions(regions)
	print "Processed regions:", ', '.join(regionToChrMap.keys())

	# chromosome configuration
	organism = config['organism(mm9 or hg19)']

	numChr = 23 if organism == 'hg19' else 20
	allChroms = ['chr' + str(x) for x in range(1,numChr)]
	allChroms.extend(['chrX', 'chrY', 'chrM'])
	threads = int(config['threads'])
	numProcs = threads

	# making bins
	for folder in folderToGraph:
		# if my bedgraph is stranded and my regions are stranded, only 
		# use the regions that correspond to the bedgraph strand
		[binFolder, graphFolder, folderStrand] = folderToGraph[folder]

		# process all regions for each sub-bedgraph
		for i in range(len(allChroms)):
			chroms = allChroms[(numProcs*i):(numProcs*(i+1))]
			reads = readBedGraph(graphFolder, chroms)
			for region in regions:
				info = regions[region]
				start, end, strandCol, numBins = int(info[4]), int(info[5]), int(info[7]), int(info[10])
				stranded = True if info[6]=='y' else False
				limitSize = True if info[9]=='y' else False
				print region, limitSize
				extendRegion = True if info[11]=='y' else False
				
				regionProcess(binFolder, region, regionToChrMap[region], chroms, start, end, stranded, folderStrand, strandCol, limitSize, numBins, extendRegion, reads)

	# merging bins for each chromosome, then make metagene

	# SNF - this code is not robust for numProcs > len(folders) 
	folders = folderToGraph.keys() 
	#numPerProc = len(folders)/numProcs + 1 
	# SNF mod - original is above, new below
	numPerProc = len(folders)/numProcs + 1 if len(folders) > numProcs else 1
	procs = []

	# SNF mod - total # of jobs to do is below, may be less than numProcs here if numProcs > len(folders) 
	numJobs = numProcs if (numProcs < len(folders)) else len(folders)

	for i in range(numJobs):  #SNF mod - replaced numProcs with numJobs 
		p = multiprocessing.Process(target=folderWorker, args=(i * numPerProc, (i + 1) * numPerProc, folders, folderToGraph, regions))
		procs.append(p)
		p.start()
	for p in procs: p.join()
	print "Made metagenes"

	# merging all files, and writing average files
	name = config["name"]
	regionToFolderAvgs = collections.defaultdict(lambda: {})
	os.chdir(parentDir)
	os.system("mkdir averages")
	for region in regions:
		for folder in folderToGraph:
			binFolder = folderToGraph[folder][0]
			os.chdir(binFolder + '/' + region + '/')
			fn = "avgraw_" + folder + "_" + region 
			regionToFolderAvgs[region][folder] = processFile(fn)
		writeFile(name + '_' + region, regionToFolderAvgs[region], parentDir + '/averages/')

if __name__ == '__main__':
	main()

