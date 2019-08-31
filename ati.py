# -*- coding: utf-8 -*-
"""
Created on Thu Jun  7 22:19:29 2018

@author: hp
"""

# This script trains the BiLSTM-CRF architecture for part-of-speech tagging using
# the universal dependency dataset (http://universaldependencies.org/).
# The code use the embeddings by Komninos et al. (https://www.cs.york.ac.uk/nlp/extvec/)
from __future__ import print_function
import os
import logging
import sys
from neuralnets.Bi import BiLSTM
from util.preprocessing import perpareDataset, loadDatasetPickle,readEmbeddings



# :: Change into the working dir of the script ::
abspath = os.path.abspath(__file__)
dname = os.path.dirname(abspath)
os.chdir(dname)

# :: Logging level ::
loggingLevel = logging.INFO
logger = logging.getLogger()
logger.setLevel(loggingLevel)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(loggingLevel)
formatter = logging.Formatter('%(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)


######################################################
#
# Data preprocessing
#
######################################################
datasets = {
    'pod1':                            #Name of the dataset
        {'columns': {0:'tokens', 1:'POS'},   #CoNLL format for the input data. Column 1 contains tokens, column 3 contains POS information
         'label': 'POS',                     #Which column we like to predict
         'evaluate': True,                   #Should we evaluate on this task? Set true always for single task setups
         'commentSymbol': None}              #Lines in the input data starting with this string will be skipped. Can be used to skip comments
}


# :: Path on your computer to the word embeddings. Embeddings by Komninos et al. will be downloaded automatically ::
embeddingsPath = 'deps.words'

# :: Prepares the dataset to be used with the LSTM-network. Creates and stores cPickle files in the pkl/ folder ::
pickleFile = perpareDataset(embeddingsPath, datasets)

######################################################
#
# The training of the network starts here
#
######################################################

#Load the embeddings and the dataset
embeddings, mappings, data = loadDatasetPickle(pickleFile)
em, word2Idx = readEmbeddings(embeddingsPath, datasets, frequencyThresholdUnknownTokens = 50, reducePretrainedEmbeddings = False)
# Some network hyperparameters
params = {'classifier': ['CRF'], 'LSTM-Size': [100], 'dropout': (0.25, 0.25)}


model = BiLSTM(params)
model.setMappings(mappings, embeddings)
model.setDataset(datasets, data)
model.buildModel()

model.trainModel()
model.storeResults('results/unidep_pos_results.csv') #Path to store performance scores for dev / test
model.modelSavePath = "models/[ModelName]_[DevScore]_[TestScore]_[Epoch].h5" #Path to store models
model.fit(epochs=25)

model.loadModel('./models/pod_0.5135_0.5493_1.h5')