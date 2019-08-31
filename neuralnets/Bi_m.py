"""
A bidirectional LSTM with optional CRF and character-based presentation for NLP sequence tagging used for multi-task learning.

Author: Nils Reimers
License: Apache-2.0
"""

from __future__ import print_function
from util import BIOF1Validation

import keras
from keras.optimizers import *
from keras.models import Model
from keras.layers import *
import math
import numpy as np
import sys
import gc
import time
import os
import random
import logging
import ast

from .keraslayers.ChainCRF import ChainCRF




class BiLSTM:
    def __init__(self, params):
        # modelSavePath = Path for storing models, resultsSavePath = Path for storing output labels while training
        self.models = None
        self.modelSavePath = None
        self.resultsSavePath = None


        # Hyperparameters for the network
        defaultParams = {'dropout': (0.5,0.5), 'classifier': ['Softmax'], 'LSTM-Size': (100,), 'customClassifier': {},
                         'optimizer': 'adam',
                         'charEmbeddings': None, 'charEmbeddingsSize': 30, 'charFilterSize': 30, 'charFilterLength': 3, 'charLSTMSize': 25, 'maxCharLength': 25,
                         'useTaskIdentifier': False, 'clipvalue': 0, 'clipnorm': 1,
                         'earlyStopping': 5, 'miniBatchSize': 32,
                         'featureNames': ['tokens', 'casing'], 'addFeatureDimensions': 10}
        if params != None:
            defaultParams.update(params)
        self.params = defaultParams



    def setMappings(self, mappings, embeddings):
        self.embeddings = embeddings
        self.mappings = mappings

    def setDataset(self, datasets, data):
        self.datasets = datasets
        self.data = data

        # Create some helping variables
        self.mainModelName = None
        self.epoch = 0
        self.learning_rate_updates = {'sgd': {1: 0.1, 3: 0.05, 5: 0.01}}    ##datasets = {'pod1':{..}}
        self.modelNames = list(self.datasets.keys())                        ##[pod1,.....]
        self.evaluateModelNames = []
        self.labelKeys = {}
        self.idx2Labels = {}
        self.trainMiniBatchRanges = None
        self.trainSentenceLengthRanges = None


        for modelName in self.modelNames:
            labelKey = self.datasets[modelName]['label']            # labelKey = POS
            self.labelKeys[modelName] = labelKey                  # labelKeys = {modelname:labelKey,...}  
            self.idx2Labels[modelName] = {v: k for k, v in self.mappings[labelKey].items()}# mappings ={tokens:{},POS:{},characters:{},casing:{}}
            #idx2Labels[pod1] = {0: 'O',1: 'B-fromloc.city_name',...value & keys of mappings[pos] interchange}
            
            if self.datasets[modelName]['evaluate']:    #if equals true
                self.evaluateModelNames.append(modelName)   #evaluateModelNames = [pod1]
            
            logging.info("--- %s ---" % modelName)
            logging.info("%d train sentences" % len(self.data[modelName]['trainMatrix']))
            logging.info("%d dev sentences" % len(self.data[modelName]['devMatrix']))
            logging.info("%d test sentences" % len(self.data[modelName]['testMatrix']))
            
        
        if len(self.evaluateModelNames) == 1:
            self.mainModelName = self.evaluateModelNames[0]  #mainModelName = pod1
             
        self.casing2Idx = self.mappings['casing']   ##casing2Idx = {....}

        
    def buildModel(self):
        self.models = {}

        tokens_input = Input(shape=(None,), dtype='int32', name='words_input')
        tokens = Embedding(input_dim=self.embeddings.shape[0], output_dim=self.embeddings.shape[1], weights=[self.embeddings], trainable=False, name='word_embeddings')(tokens_input)
        #Embedding(input_dim,output_dim,inputlength..)

        inputNodes = [tokens_input]
        mergeInputLayers = [tokens]

        for featureName in self.params['featureNames']: ##'featureNames': ['tokens', 'casing']
            if featureName == 'tokens' or featureName == 'characters':
                continue

            feature_input = Input(shape=(None,), dtype='int32', name=featureName+'_input') ##featureName = casing
            feature_embedding = Embedding(input_dim=len(self.mappings[featureName]), output_dim=self.params['addFeatureDimensions'], name=featureName+'_emebddings')(feature_input)

            inputNodes.append(feature_input)    # inputNodes = [tokens_input,feature_input]
            mergeInputLayers.append(feature_embedding) #mergeInputLayers = [tokens,feature_embedding]
        

        # :: Character Embeddings :: (---NOT EXECUTED)
            """  if self.params['charEmbeddings'] not in [None, "None", "none", False, "False", "false"]: #charEmbeddings:None
            logging.info("Pad words to uniform length for characters embeddings")
            all_sentences = []
            for dataset in self.data.values():
                for data in [dataset['trainMatrix'], dataset['devMatrix'], dataset['testMatrix']]:
                    for sentence in data:
                        all_sentences.append(sentence)

            self.padCharacters(all_sentences)
            logging.info("Words padded to %d characters" % (self.maxCharLen))
            
            charset = self.mappings['characters']
            charEmbeddingsSize = self.params['charEmbeddingsSize']
            maxCharLen = self.maxCharLen
            charEmbeddings= []
            for _ in charset:
                limit = math.sqrt(3.0/charEmbeddingsSize)
                vector = np.random.uniform(-limit, limit, charEmbeddingsSize) 
                charEmbeddings.append(vector)
                
            charEmbeddings[0] = np.zeros(charEmbeddingsSize) #Zero padding
            charEmbeddings = np.asarray(charEmbeddings)
            
            chars_input = Input(shape=(None,maxCharLen), dtype='int32', name='char_input')
            chars = TimeDistributed(Embedding(input_dim=charEmbeddings.shape[0], output_dim=charEmbeddings.shape[1],  weights=[charEmbeddings], trainable=True, mask_zero=True), name='char_emd')(chars_input)
            
            if self.params['charEmbeddings'].lower() == 'lstm': #Use LSTM for char embeddings from Lample et al., 2016
                charLSTMSize = self.params['charLSTMSize']
                chars = TimeDistributed(Bidirectional(LSTM(charLSTMSize, return_sequences=False)), name="char_lstm")(chars)
            else: #Use CNNs for character embeddings from Ma and Hovy, 2016
                charFilterSize = self.params['charFilterSize']
                charFilterLength = self.params['charFilterLength']
                chars = TimeDistributed(Conv1D(charFilterSize, charFilterLength, padding='same'), name="char_cnn")(chars)
                chars = TimeDistributed(GlobalMaxPooling1D(), name="char_pooling")(chars)
            
            mergeInputLayers.append(chars)
            inputNodes.append(chars_input)
            self.params['featureNames'].append('characters')
            
        # :: Task Identifier :: 
        if self.params['useTaskIdentifier']:    #'useTaskIdentifier': False
            self.addTaskIdentifier()
            
            taskID_input = Input(shape=(None,), dtype='int32', name='task_id_input')
            taskIDMatrix = np.identity(len(self.modelNames), dtype='float32')
            taskID_outputlayer = Embedding(input_dim=taskIDMatrix.shape[0], output_dim=taskIDMatrix.shape[1], weights=[taskIDMatrix], trainable=False, name='task_id_embedding')(taskID_input)
        
            mergeInputLayers.append(taskID_outputlayer)
            inputNodes.append(taskID_input)
            self.params['featureNames'].append('taskID') """

        if len(mergeInputLayers) >= 2: #true here
            merged_input = concatenate(mergeInputLayers) #mergeInputLayers = [tokens,feature_embedding]
        else:
            merged_input = mergeInputLayers[0]
        
        
        # Add LSTMs
        shared_layer = merged_input
        logging.info("LSTM-Size: %s" % str(self.params['LSTM-Size']))   #'LSTM-Size': [100]
        cnt = 1
        for size in self.params['LSTM-Size']:      
            if isinstance(self.params['dropout'], (list, tuple)):  #true- 'dropout': (0.25, 0.25)
                shared_layer = Bidirectional(LSTM(size, return_sequences=True, dropout=self.params['dropout'][0], recurrent_dropout=self.params['dropout'][1]), name='shared_varLSTM_'+str(cnt))(shared_layer)
            else:
                """ Naive dropout """
                shared_layer = Bidirectional(LSTM(size, return_sequences=True), name='shared_LSTM_'+str(cnt))(shared_layer) 
                if self.params['dropout'] > 0.0:
                    shared_layer = TimeDistributed(Dropout(self.params['dropout']), name='shared_dropout_'+str(self.params['dropout'])+"_"+str(cnt))(shared_layer)
            
            cnt += 1
            
            
        for modelName in self.modelNames:
            output = shared_layer #shared_layer = Bidirectional(LSTM(size,return_sequences=True...)
            
            modelClassifier = self.params['customClassifier'][modelName] if modelName in self.params['customClassifier'] else self.params['classifier'] #else part true

            if not isinstance(modelClassifier, (tuple, list)): #'classifier': ['CRF']
                modelClassifier = [modelClassifier]
            
            cnt = 1
            for classifier in modelClassifier:
                n_class_labels = len(self.mappings[self.labelKeys[modelName]]) # labelKeys = {modelname:labelKey,...}
                # n_class_labels = 1 (here)

                if classifier == 'Softmax':
                    output = TimeDistributed(Dense(n_class_labels, activation='softmax'), name=modelName+'_softmax')(output)
                    lossFct = 'sparse_categorical_crossentropy'
                elif classifier == 'CRF':   #true
                    output = TimeDistributed(Dense(n_class_labels, activation=None), ###???
                                             name=modelName + '_hidden_lin_layer')(output)###????
                    crf = ChainCRF(name=modelName+'_crf')
                    output = crf(output)
                    lossFct = crf.sparse_loss
                elif isinstance(classifier, (list, tuple)) and classifier[0] == 'LSTM':
                            
                    size = classifier[1]
                    if isinstance(self.params['dropout'], (list, tuple)): 
                        output = Bidirectional(LSTM(size, return_sequences=True, dropout=self.params['dropout'][0], recurrent_dropout=self.params['dropout'][1]), name=modelName+'_varLSTM_'+str(cnt))(output)
                    else:
                        """ Naive dropout """ 
                        output = Bidirectional(LSTM(size, return_sequences=True), name=modelName+'_LSTM_'+str(cnt))(output) 
                        if self.params['dropout'] > 0.0:
                            output = TimeDistributed(Dropout(self.params['dropout']), name=modelName+'_dropout_'+str(self.params['dropout'])+"_"+str(cnt))(output)                    
                else:
                    assert(False) #Wrong classifier
                    
                cnt += 1
                
            # :: Parameters for the optimizer ::
            optimizerParams = {} #'clipnorm': 1 , clipvalue: 0
            if 'clipnorm' in self.params and self.params['clipnorm'] != None and  self.params['clipnorm'] > 0: #true
                optimizerParams['clipnorm'] = self.params['clipnorm']
            
            if 'clipvalue' in self.params and self.params['clipvalue'] != None and  self.params['clipvalue'] > 0:#false
                optimizerParams['clipvalue'] = self.params['clipvalue']
            
        #optimizerParams = {'clipnorm': 1}, All parameter gradients will be clipped to a maximum norm of 1.

            #'optimizer': 'adam 
            
            if self.params['optimizer'].lower() == 'adam':  #true
                opt = Adam(**optimizerParams)               #????
            elif self.params['optimizer'].lower() == 'nadam':
                opt = Nadam(**optimizerParams)
            elif self.params['optimizer'].lower() == 'rmsprop': 
                opt = RMSprop(**optimizerParams)
            elif self.params['optimizer'].lower() == 'adadelta':
                opt = Adadelta(**optimizerParams)
            elif self.params['optimizer'].lower() == 'adagrad':
                opt = Adagrad(**optimizerParams)
            elif self.params['optimizer'].lower() == 'sgd':
                opt = SGD(lr=0.1, **optimizerParams)
            
            
            model = Model(inputs=inputNodes, outputs=[output]) ## inputNodes = [tokens_input,feature_input]
            model.compile(loss=lossFct, optimizer=opt)
            
            model.summary(line_length=200)
            #logging.info(model.get_config())
            #logging.info("Optimizer: %s - %s" % (str(type(model.optimizer)), str(model.optimizer.get_config())))
            
            self.models[modelName] = model
        


    def trainModel(self):
        self.epoch += 1
        
        #learning_rate_updates = {'sgd': {1: 0.1, 3: 0.05, 5: 0.01}} 
        
        if self.params['optimizer'] in self.learning_rate_updates and self.epoch in self.learning_rate_updates[self.params['optimizer']]:       
            logging.info("Update Learning Rate to %f" % (self.learning_rate_updates[self.params['optimizer']][self.epoch]))
            for modelName in self.modelNames:            
                K.set_value(self.models[modelName].optimizer.lr, self.learning_rate_updates[self.params['optimizer']][self.epoch])  #???
                #The learning rate is a variable on the computing deviceThat means that you have to use K.set_value, with K being keras.backend
                #import keras.backend as K
                # K.set_value(opt.lr, 0.01)
                
            
        for batch in self.minibatch_iterate_dataset():
            for modelName in self.modelNames:         
                nnLabels = batch[modelName][0]      #batch = [pod1: [nnlabels,...nnInput...]]
                nnInput = batch[modelName][1:]
                self.models[modelName].train_on_batch(nnInput, nnLabels) 
                ##train_on_batch - Runs a single gradient update on a single batch of data
                #returns a list of scalars (if the model has multiple outputs and/or metrics)
                
                               
            
          

    def minibatch_iterate_dataset(self, modelNames = None):
        """ Create based on sentence length mini-batches with approx. the same size. Sentences and 
        mini-batch chunks are shuffled and used to the train the model """
        
        if self.trainSentenceLengthRanges == None: #true 
            """ Create mini batch ranges """
            self.trainSentenceLengthRanges = {}
            self.trainMiniBatchRanges = {}            
            for modelName in self.modelNames:
                trainData = self.data[modelName]['trainMatrix'] #data = {pod1: 'trainMatrix':{}}
                trainData.sort(key=lambda x:len(x['tokens'])) #Sort train matrix by sentence length
                #list.sort(key=..., reverse=...)....key - function that serves as a key for the sort comparison
                trainRanges = []
                oldSentLength = len(trainData[0]['tokens'])   #smallest token length         
                idxStart = 0
                
                #Find start and end of ranges with sentences with same length
                for idx in range(len(trainData)):
                    sentLength = len(trainData[idx]['tokens'])
                    
                    if sentLength != oldSentLength:
                        trainRanges.append((idxStart, idx)) # trainRanges = [(0,1),(1,2)....]
                        idxStart = idx
                    
                    oldSentLength = sentLength
                
                #Add last sentence
                print (trainRanges.append((idxStart, len(trainData)))) ###??? why???
                
                
                #Break up ranges into smaller mini batch sizes
                miniBatchRanges = []
                for batchRange in trainRanges:
                    rangeLen = batchRange[1]-batchRange[0]

                    bins = int(math.ceil(rangeLen/float(self.params['miniBatchSize']))) #miniBatchSize: 32 ,maybe bins = 1
                    binSize = int(math.ceil(rangeLen / float(bins)))   ###maybe...binSize - rangeLen   
                    
                    for binNr in range(bins):
                        startIdx = binNr*binSize+batchRange[0]
                        endIdx = min(batchRange[1],(binNr+1)*binSize+batchRange[0])
                        miniBatchRanges.append((startIdx, endIdx))  # miniBatchRanges = [(0,1),(1,2),...]
                      
                self.trainSentenceLengthRanges[modelName] = trainRanges # trainRanges = [(0,1),(1,2)....,(8,10),..]
                self.trainMiniBatchRanges[modelName] = miniBatchRanges
                
        if modelNames == None:
            modelNames = self.modelNames
            
        #Shuffle training data
        for modelName in modelNames:      
            #1. Shuffle sentences that have the same length
            x = self.data[modelName]['trainMatrix']
            for dataRange in self.trainSentenceLengthRanges[modelName]:
                for i in reversed(range(dataRange[0]+1, dataRange[1])): #i will run from (dataRange[0]+1)(included)  to  dataRange[1](exluded) in reverse order
                    # pick an element in x[:i+1] with which to exchange x[i]
                    j = random.randint(dataRange[0], i) # Return a random integer N such that a <= N <= b
                    x[i], x[j] = x[j], x[i]
               
            #2. Shuffle the order of the mini batch ranges       
            random.shuffle(self.trainMiniBatchRanges[modelName])
     
        
        #Iterate over the mini batch ranges
        if self.mainModelName != None:   # true - mainModelName = pod1 
            rangeLength = len(self.trainMiniBatchRanges[self.mainModelName])
        else:
            rangeLength = min([len(self.trainMiniBatchRanges[modelName]) for modelName in modelNames])

        
        batches = {}
        for idx in range(rangeLength):
            batches.clear()     #The clear() method removes all items from the list.
            
            for modelName in modelNames:   
                trainMatrix = self.data[modelName]['trainMatrix']
                dataRange = self.trainMiniBatchRanges[modelName][idx % len(self.trainMiniBatchRanges[modelName])] 
                labels = np.asarray([trainMatrix[idx][self.labelKeys[modelName]] for idx in range(dataRange[0], dataRange[1])])
                labels = np.expand_dims(labels, -1)
                
                trainMatrix[0]['tokens']  = ast.literal_eval(trainMatrix[0]['tokens'])
                
                batches[modelName] = [labels]
                
                for featureName in self.params['featureNames']: ##'featureNames': ['tokens', 'casing',...]
                    if featureName == 'tokens':
                        j = []
                        for idx in range(dataRange[0], dataRange[1]):
                            j.extend(trainMatrix[idx][featureName])
                        ##j.extend(trainMatrix[idx][featureName] for idx in range(dataRange[0], dataRange[1]))
                        inputData = np.asarray(j)    
                        #inputData = np.asarray([trainMatrix[idx][featureName] for idx in range(dataRange[0], dataRange[1])])    
                    else:
                        inputData = np.asarray([trainMatrix[idx][featureName] for idx in range(dataRange[0], dataRange[1])])
                    batches[modelName].append(inputData)
            
            yield batches   
            

        
    def storeResults(self, resultsFilepath): # resultsFilepath = results/unidep_pos_results.csv'
        if resultsFilepath != None:
            directory = os.path.dirname(resultsFilepath)
            if not os.path.exists(directory):
                os.makedirs(directory)
                
            self.resultsSavePath = open(resultsFilepath, 'w')
        else:
            self.resultsSavePath = None
        
    def fit(self, epochs):
        if self.models is None:
            self.buildModel()

        total_train_time = 0
        max_dev_score = {modelName:0 for modelName in self.models.keys()}
        max_test_score = {modelName:0 for modelName in self.models.keys()}
        no_improvement_since = 0
        
        for epoch in range(epochs):      
            sys.stdout.flush()           
            logging.info("\n--------- Epoch %d -----------" % (epoch+1))
            
            start_time = time.time() 
            self.trainModel()   ##????
            time_diff = time.time() - start_time
            total_train_time += time_diff
            logging.info("%.2f sec for training (%.2f total)" % (time_diff, total_train_time))
            
            
            start_time = time.time() 
            for modelName in self.evaluateModelNames:
                logging.info("-- %s --" % (modelName))
                dev_score, test_score = self.computeScore(modelName, self.data[modelName]['devMatrix'], self.data[modelName]['testMatrix'])
         
                
                if dev_score > max_dev_score[modelName]:
                    max_dev_score[modelName] = dev_score    # max_dev_score = {pod1:....}
                    max_test_score[modelName] = test_score
                    no_improvement_since = 0

                    #Save the model
                    if self.modelSavePath != None:
                        self.saveModel(modelName, epoch, dev_score, test_score)
                else:
                    no_improvement_since += 1
                    
                    
                if self.resultsSavePath != None:
                    self.resultsSavePath.write("\t".join(map(str, [epoch + 1, modelName, dev_score, test_score, max_dev_score[modelName], max_test_score[modelName]])))
                    # maps each element of list to str function ,The str() method returns a string 
                    self.resultsSavePath.write("\n")
                    self.resultsSavePath.flush()
                
                logging.info("Max: %.4f dev; %.4f test" % (max_dev_score[modelName], max_test_score[modelName]))
                logging.info("")
                
            logging.info("%.2f sec for evaluation" % (time.time() - start_time))
            
            if self.params['earlyStopping']  > 0 and no_improvement_since >= self.params['earlyStopping']: #earlyStopping: 5
                logging.info("!!! Early stopping, no improvement after "+str(no_improvement_since)+" epochs !!!")
                break
            
            
    def tagSentences(self, sentences):
        # Pad characters
       """ if 'characters' in self.params['featureNames']:
            self.padCharacters(sentences)"""

        labels = {}
        for modelName, model in self.models.items():
            paddedPredLabels = self.predictLabels(model, sentences)
            predLabels = []
            for idx in range(len(sentences)):
                unpaddedPredLabels = []
                for tokenIdx in range(len(sentences[idx]['tokens'])):
                    if sentences[idx]['tokens'][tokenIdx] != 0:  # Skip padding tokens
                        unpaddedPredLabels.append(paddedPredLabels[idx][tokenIdx])

                predLabels.append(unpaddedPredLabels)

            idx2Label = self.idx2Labels[modelName]
            labels[modelName] = [[idx2Label[tag] for tag in tagSentence] for tagSentence in predLabels]

        return labels
            
    
    def getSentenceLengths(self, sentences):
        sentenceLengths = {}
        for idx in range(len(sentences)):
            sentence = sentences[idx]['tokens']
            #for j in range(len(sentences[idx]['tokens'])):
                #sentence = sentences[idx]['tokens'][j]
            if len(sentence) not in sentenceLengths:
                sentenceLengths[len(sentence)] = []
            sentenceLengths[len(sentence)].append(idx)  #sentenceLengths = {length1: [idx1,idx2,.] ,...}
        
        return sentenceLengths

    def predictLabels(self, model, sentences):
        predLabels = [None]*len(sentences)  # predLabels = [....] (no of element = len(sentences))
        sentenceLengths = self.getSentenceLengths(sentences)
        
         sentences[0]['tokens'] = ast.literal_eval(sentences[0]['tokens'])
        
        for indices in sentenceLengths.values():   
            nnInput = []                  
            for featureName in self.params['featureNames']:
                if featureName == 'tokens':
                    s = []
                    for idx in indices:
                        s.extend(sentences[idx][featureName])                        
                    inputData = np.asarray(s)
                    #inputData = np.asarray([sentences[idx][featureName] for idx in indices])
                else:
                    inputData = np.asarray([sentences[idx][featureName] for idx in indices])
                nnInput.append(inputData)
            
            predictions = model.predict(nnInput, verbose=False)
            predictions = predictions.argmax(axis=-1) #Predict classes            
           
            
            predIdx = 0
            for idx in indices:
                predLabels[idx] = predictions[predIdx]      #predLabels = [idx1: pred1,....]
                predIdx += 1   
        
        return predLabels
    
   
    def computeScore(self, modelName, devMatrix, testMatrix):
        if self.labelKeys[modelName].endswith('_BIO') or self.labelKeys[modelName].endswith('_IOBES') or self.labelKeys[modelName].endswith('_IOB'):
            return self.computeF1Scores(modelName, devMatrix, testMatrix)
        else:   #true(here) ---labelKeys = {modelname:labelKey,...}
            return self.computeAccScores(modelName, devMatrix, testMatrix)   

    def computeF1Scores(self, modelName, devMatrix, testMatrix):
        #train_pre, train_rec, train_f1 = self.computeF1(modelName, self.datasets[modelName]['trainMatrix'])
        #print "Train-Data: Prec: %.3f, Rec: %.3f, F1: %.4f" % (train_pre, train_rec, train_f1)
        
        dev_pre, dev_rec, dev_f1 = self.computeF1(modelName, devMatrix)
        logging.info("Dev-Data: Prec: %.3f, Rec: %.3f, F1: %.4f" % (dev_pre, dev_rec, dev_f1))
        
        test_pre, test_rec, test_f1 = self.computeF1(modelName, testMatrix)
        logging.info("Test-Data: Prec: %.3f, Rec: %.3f, F1: %.4f" % (test_pre, test_rec, test_f1))
        
        return dev_f1, test_f1
    
    def computeAccScores(self, modelName, devMatrix, testMatrix):
        dev_acc = self.computeAcc(modelName, devMatrix)
        test_acc = self.computeAcc(modelName, testMatrix)
        
        logging.info("Dev-Data: Accuracy: %.4f" % (dev_acc))
        logging.info("Test-Data: Accuracy: %.4f" % (test_acc))
        
        return dev_acc, test_acc   
        
        
    def computeF1(self, modelName, sentences):
        labelKey = self.labelKeys[modelName]
        model = self.models[modelName]
        idx2Label = self.idx2Labels[modelName]
        
        correctLabels = [sentences[idx][labelKey] for idx in range(len(sentences))]
        predLabels = self.predictLabels(model, sentences) 

        labelKey = self.labelKeys[modelName]
        encodingScheme = labelKey[labelKey.index('_')+1:]
        
        pre, rec, f1 = BIOF1Validation.compute_f1(predLabels, correctLabels, idx2Label, 'O', encodingScheme)
        pre_b, rec_b, f1_b = BIOF1Validation.compute_f1(predLabels, correctLabels, idx2Label, 'B', encodingScheme)
        
        if f1_b > f1:
            logging.debug("Setting wrong tags to B- improves from %.4f to %.4f" % (f1, f1_b))
            pre, rec, f1 = pre_b, rec_b, f1_b
        
        return pre, rec, f1
    
    def computeAcc(self, modelName, sentences):
        correctLabels = [sentences[idx][self.labelKeys[modelName]] for idx in range(len(sentences))]
        predLabels = self.predictLabels(self.models[modelName], sentences) 
        
        numLabels = 0
        numCorrLabels = 0
        for sentenceId in range(len(correctLabels)):
            for tokenId in range(len(correctLabels[sentenceId])):
                numLabels += 1
                if correctLabels[sentenceId][tokenId] == predLabels[sentenceId][tokenId]:
                    numCorrLabels += 1

  
        return numCorrLabels/float(numLabels)
    
    def padCharacters(self, sentences):
        """ Pads the character representations of the words to the longest word in the dataset """
        #Find the longest word in the dataset
        maxCharLen = self.params['maxCharLength']
        if maxCharLen <= 0:
            for sentence in sentences:
                for token in sentence['characters']:
                    maxCharLen = max(maxCharLen, len(token))
          

        for sentenceIdx in range(len(sentences)):
            for tokenIdx in range(len(sentences[sentenceIdx]['characters'])):
                token = sentences[sentenceIdx]['characters'][tokenIdx]

                if len(token) < maxCharLen: #Token shorter than maxCharLen -> pad token
                    sentences[sentenceIdx]['characters'][tokenIdx] = np.pad(token, (0,maxCharLen-len(token)), 'constant')
                else: #Token longer than maxCharLen -> truncate token
                    sentences[sentenceIdx]['characters'][tokenIdx] = token[0:maxCharLen]
    
        self.maxCharLen = maxCharLen
        
    def addTaskIdentifier(self):
        """ Adds an identifier to every token, which identifies the task the token stems from """
        taskID = 0
        for modelName in self.modelNames:
            dataset = self.data[modelName]
            for dataName in ['trainMatrix', 'devMatrix', 'testMatrix']:            
                for sentenceIdx in range(len(dataset[dataName])):
                    dataset[dataName][sentenceIdx]['taskID'] = [taskID] * len(dataset[dataName][sentenceIdx]['tokens'])
            
            taskID += 1


    def saveModel(self, modelName, epoch, dev_score, test_score):
        import json
        import h5py

        if self.modelSavePath == None:
            raise ValueError('modelSavePath not specified.')

        savePath = self.modelSavePath.replace("[DevScore]", "%.4f" % dev_score).replace("[TestScore]", "%.4f" % test_score).replace("[Epoch]", str(epoch+1)).replace("[ModelName]", modelName)

        directory = os.path.dirname(savePath)
        if not os.path.exists(directory):
            os.makedirs(directory)

        if os.path.isfile(savePath):
            logging.info("Model "+savePath+" already exists. Model will be overwritten")

        self.models[modelName].save(savePath, True)

        with h5py.File(savePath, 'a') as h5file:
            h5file.attrs['mappings'] = json.dumps(self.mappings) #"json.dumps()" returns a string
            h5file.attrs['params'] = json.dumps(self.params)
            h5file.attrs['modelName'] = modelName
            h5file.attrs['labelKey'] = self.datasets[modelName]['label']




    @staticmethod
    def loadModel(modelPath):
        import h5py
        import json
        from .keraslayers.ChainCRF import create_custom_objects

        model = keras.models.load_model(modelPath, custom_objects=create_custom_objects())

        with h5py.File(modelPath, 'r') as f:
            mappings = json.loads(f.attrs['mappings'])
            params = json.loads(f.attrs['params'])
            modelName = f.attrs['modelName']
            labelKey = f.attrs['labelKey']

        bilstm = BiLSTM(params)
        bilstm.setMappings(mappings, None)
        bilstm.models = {modelName: model}
        bilstm.labelKeys = {modelName: labelKey}
        bilstm.idx2Labels = {}
        bilstm.idx2Labels[modelName] = {v: k for k, v in bilstm.mappings[labelKey].items()}
        return bilstm