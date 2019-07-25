#!/usr/bin/env python

"""DIVERSITY SAMPLING
 
Diversity Sampling examples for Active Learning in PyTorch 

This is an open source example to accompany Chapter 4 from the book:
"Human-in-the-Loop Machine Learning"

This example tries to classify news headlines into one of two categories:
  disaster-related
  not disaster-related

It contains four Active Learning strategies:
1. Model-based outlier sampling
2. Cluster-based sampling
3. Representative sampling
4. Adaptive Representative sampling

You can uncomment the relevant calling code at the end of this file for each strategy


"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import random
import math
import datetime
import csv
import re
import os
import getopt

from random import shuffle
from collections import defaultdict	
from numpy import rank

import uncertainty_sampling_pytorch
from pytorch_clusters import CosineClusters 
from pytorch_clusters import Cluster



__author__ = "Robert Munro"
__license__ = "MIT"
__version__ = "1.0.1"

# settings

minimum_evaluation_items = 1200 # annotate this many randomly sampled items first for evaluation data before creating training data
minimum_validation_items = 200 # annotate this many randomly sampled items first for validation data before creating training data
minimum_training_items = 100 # minimum number of training items before we first train a model

epochs = 10 # number of epochs per training session
select_per_epoch = 200  # number to select per epoch per label


data = []
test_data = []

# directories with data
unlabeled_data = "unlabeled_data/unlabeled_data.csv"

evaluation_related_data = "evaluation_data/related.csv"
evaluation_not_related_data = "evaluation_data/not_related.csv"

validation_related_data  = "validation_data/related.csv" 
validation_not_related_data = "validation_data/not_related.csv" 

training_related_data = "training_data/related.csv"
training_not_related_data = "training_data/not_related.csv"


already_labeled = {} # tracking what is already labeled
feature_index = {} # feature mapping for one-hot encoding



def load_data(filepath, skip_already_labeled=False):
    # csv format: [ID, TEXT, LABEL, SAMPLING_STRATEGY, CONFIDENCE]
    with open(filepath, 'r') as csvfile:
        data = []
        reader = csv.reader(csvfile)
        for row in reader:
            if skip_already_labeled and row[0] in already_labeled:
        	    continue
        		
            if len(row) < 3:
                row.append("") # add empty col for LABEL to add later
            if len(row) < 4:
                row.append("") # add empty col for SAMPLING_STRATEGY to add later        
            if len(row) < 5:
                row.append(0) # add empty col for CONFIDENCE to add later         
            data.append(row)

            label = str(row[2])
            if row[2] != "":
                textid = row[0]
                already_labeled[textid] = label

    csvfile.close()
    return data

def append_data(filepath, data):
    with open(filepath, 'a', errors='replace') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(data)
    csvfile.close()

def write_data(filepath, data):
    with open(filepath, 'w', errors='replace') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(data)
    csvfile.close()


# LOAD ALL UNLABELED, TRAINING, VALIDATION, AND EVALUATION DATA
training_data = load_data(training_related_data) + load_data(training_not_related_data)
training_count = len(training_data)

validation_data = load_data(validation_related_data) + load_data(validation_not_related_data)
validation_count = len(validation_data)
    
evaluation_data = load_data(evaluation_related_data) + load_data(evaluation_not_related_data)
evaluation_count = len(evaluation_data)

data = load_data(unlabeled_data, skip_already_labeled=True)

annotation_instructions = "Please type 1 if this message is disaster-related, "
annotation_instructions += "or hit Enter if not.\n"
annotation_instructions += "Type 2 to go back to the last message, "
annotation_instructions += "type d to see detailed definitions, "
annotation_instructions += "or type s to save your annotations.\n"

last_instruction = "All done!\n"
last_instruction += "Type 2 to go back to change any labels,\n"
last_instruction += "or Enter to save your annotations."

detailed_instructions = "A 'disaster-related' headline is any story about a disaster.\n"
detailed_instructions += "It includes:\n"
detailed_instructions += "  - human, animal and plant disasters.\n"
detailed_instructions += "  - the response to disasters (aid).\n"
detailed_instructions += "  - natural disasters and man-made ones like wars.\n"
detailed_instructions += "It does not include:\n"
detailed_instructions += "  - criminal acts and non-disaster-related police work\n"
detailed_instructions += "  - post-response activity like disaster-related memorials.\n\n"


def get_annotations(data, default_sampling_strategy="random"):
    """Prompts annotator for label from command line and adds annotations to data 
    
    Keyword arguments:
        data -- an list of unlabeled items where each item is 
                [ID, TEXT, LABEL, SAMPLING_STRATEGY, CONFIDENCE]
        default_sampling_strategy -- strategy to use for each item if not already specified
    """

    ind = 0
    while ind <= len(data):
        if ind < 0:
            ind = 0 # in case you've gone back before the first
        if ind < len(data):
            textid = data[ind][0]
            text = data[ind][1]
            label = data[ind][2]
            strategy =  data[ind][3]

            if textid in already_labeled:
                print("Skipping seen "+label)
                ind+=1
            else:
                print(annotation_instructions)
                label = str(input(text+"\n\n> ")) 

                if label == "2":                   
                    ind-=1  # go back
                elif label == "d":                    
                    print(detailed_instructions) # print detailed instructions
                elif label == "s":
                    break  # save and exit
                else:
                    if not label == "1":
                        label = "0" # treat everything other than 1 as 0
                        
                    data[ind][2] = label # add label to our data

                    if data[ind][3] is None or data[ind][3] == "":
                        data[ind][3] = default_sampling_strategy # add default if none given
                    ind+=1        

        else:
            #last one - give annotator a chance to go back
            print(last_instruction)
            label = str(input("\n\n> ")) 
            if label == "2":
                ind-=1
            else:
                ind+=1

    return data


def create_features(minword = 3):
    """Create indexes for one-hot encoding of words in files
    
    """

    total_training_words = {}
    for item in data + training_data:
        text = item[1]
        for word in text.split():
            if word not in total_training_words:
                total_training_words[word] = 1
            else:
                total_training_words[word] += 1

    for item in data + training_data:
        text = item[1]
        for word in text.split():
            if word not in feature_index and total_training_words[word] >= minword:
                feature_index[word] = len(feature_index)

    return len(feature_index)


class SimpleTextClassifier(nn.Module):  # inherit pytorch's nn.Module
    """Text Classifier with 1 hidden layer 

    """
    
    def __init__(self, num_labels, vocab_size):
        super(SimpleTextClassifier, self).__init__() # call parent init

        # Define model with one hidden layer with 128 neurons
        self.linear1 = nn.Linear(vocab_size, 128)
        self.linear2 = nn.Linear(128, num_labels)

    def forward(self, feature_vec, return_all_layers=False):
        # Define how data is passed through the model and what gets returned

        hidden1 = self.linear1(feature_vec).clamp(min=0) # ReLU
        output = self.linear2(hidden1)
        log_softmax = F.log_softmax(output, dim=1)

        if return_all_layers:
            return [hidden1, output, log_softmax]
        else:
            return log_softmax
                                

def make_feature_vector(features, feature_index):
    vec = torch.zeros(len(feature_index))
    for feature in features:
        if feature in feature_index:
            vec[feature_index[feature]] += 1
    return vec.view(1, -1)


def train_model(training_data, validation_data = "", evaluation_data = "", num_labels=2, vocab_size=0):
    """Train model on the given training_data

    Tune with the validation_data
    Evaluate accuracy with the evaluation_data
    """

    model = SimpleTextClassifier(num_labels, vocab_size)
    # let's hard-code our labels for this example code 
    # and map to the same meaningful booleans in our data, 
    # so we don't mix anything up when inspecting our data
    label_to_ix = {"not_disaster_related": 0, "disaster_related": 1} 

    loss_function = nn.NLLLoss()
    optimizer = optim.SGD(model.parameters(), lr=0.01)

    # epochs training
    for epoch in range(epochs):
        print("Epoch: "+str(epoch))
        current = 0

        # make a subset of data to use in this epoch
        # with an equal number of items from each label

        shuffle(training_data) #randomize the order of the training data        
        related = [row for row in training_data if '1' in row[2]]
        not_related = [row for row in training_data if '0' in row[2]]
        
        epoch_data = related[:select_per_epoch]
        epoch_data += not_related[:select_per_epoch]
        shuffle(epoch_data) 
                
        # train our model
        for item in epoch_data:
            training_idx = random.randint(0,len(data)-1)
            features = item[1].split()
            label = int(item[2])

            model.zero_grad() 

            feature_vec = make_feature_vector(features, feature_index)
            target = torch.LongTensor([int(label)])

            log_probs = model(feature_vec)

			# compute loss function, do backward pass, and update the gradient
            loss = loss_function(log_probs, target)
            loss.backward()
            optimizer.step()	

    fscore, auc = evaluate_model(model, evaluation_data)
    fscore = round(fscore,3)
    auc = round(auc,3)

    # save model to path that is alphanumeric and includes number of items and accuracies in filename
    timestamp = re.sub('\.[0-9]*','_',str(datetime.datetime.now())).replace(" ", "_").replace("-", "").replace(":","")
    training_size = "_"+str(len(training_data))
    accuracies = str(fscore)+"_"+str(auc)
                     
    model_path = "models/"+timestamp+accuracies+training_size+".params"

    torch.save(model.state_dict(), model_path)
    return model_path



def get_random_items(unlabeled_data, number = 10):
    shuffle(unlabeled_data)

    random_items = []
    for item in unlabeled_data:
        textid = item[0]
        if textid in already_labeled:
            continue
        item[3] = "random_remaining"
        random_items.append(item)
        if len(random_items) >= number:
            break

    return random_items


def get_rank(value, rankings):
    """ get the rank of the value in an ordered array as a percentage 
    
        returns linear distance between the indexes where value occurs    
    """
    
    index = 0 # default: ranking = 0
    
    for ranked_number in rankings:
        if value < ranked_number:
            break #NB: this O(N) loop could be optimized to O(log(N))
        index += 1        
    
    if(index >= len(rankings)):
        index = len(rankings) # maximum: ranking = 1
        
    elif(index > 0):
        # get linear interpolation between the two closest indexes 
        
        diff = rankings[index] - rankings[index - 1]
        perc = value - rankings[index - 1]
        linear = perc / diff
        index = float(index - 1) + linear
    
    absolute_ranking = index / len(rankings)

    return(absolute_ranking)



def get_cluster_samples(data, num_clusters=5, max_epochs=5, limit=5000):
    """Create clusters using cosine similarity
    
    Creates clusters by the K-Means clustering algorithm,
    using cosine similarity instead of more common euclidean distance
    
    Creates num_clusters clusters (default 20)
    until converged or max_epochs passes over the data 
    
    Limits to the first limit items, or limit = -1 means no limit
    
    """ 
    
    if limit > 0:
        shuffle(data)
        data = data[:limit]
    
    cosine_clusters = CosineClusters(num_clusters)
    
    cosine_clusters.add_random_training_items(data)
    
    for i in range(0, max_epochs):
        print("Epoch "+str(i))
        added = cosine_clusters.add_items_to_best_cluster(data)
        if added == 0:
            break

    centroids = cosine_clusters.get_centroids()
    outliers = cosine_clusters.get_outliers()
    randoms = cosine_clusters.get_randoms()
    
    return centroids + outliers + randoms
         

def get_representative_samples(training_data, unlabeled_data, number=20, limit=10000):
    """Gets the most representative unlabeled items, compared to training data
    
    Creates one cluster for each data set 
    
    returns number items 
    
    Limits to the first limit items, or limit = -1 means no limit
    
    """ 
        
    if limit > 0:
        shuffle(training_data)
        training_data = training_data[:limit]
        shuffle(unlabeled_data)
        unlabeled_data = unlabeled_data[:limit]
        
    training_cluster = Cluster()
    for item in training_data:
        training_cluster.add_to_cluster(item)
    
    unlabeled_cluster = Cluster()    
    for item in unlabeled_data:
        unlabeled_cluster.add_to_cluster(item)

    
    for item in unlabeled_data:
        training_score = training_cluster.cosine_similary(item)
        unlabeled_score = unlabeled_cluster.cosine_similary(item)
        
        representativeness = unlabeled_score - training_score
        
        item[3] = "representative"            
        item[4] = representativeness
            
                 
    unlabeled_data.sort(reverse=True, key=lambda x: x[4])       
    return unlabeled_data[:number:]       


def get_adaptive_representative_samples(training_data, unlabeled_data, number=20, limit=5000):
    samples = []
    
    for i in range(0, number):
        print("Epoch "+str(i))
        representative_item = get_representative_samples(training_data, unlabeled_data, 1, limit)[0]
        samples.append(representative_item)
        unlabeled_data.remove(representative_item)
        
    return samples


def get_model_outliers(model, unlabeled_data, validation_data, number=5, limit=10000):
    """Get outliers from unlabeled data in training data

    Returns number outliers                                                                                

    An outlier is defined as 
    unlabeled_data with the lowest average from rank order of logits
    where rank order is defined by validation data inference 

    """

    validation_rankings = [] # 2D array, every neuron by ordered list of output on validation data per neuron    

    # Step 1: get per-neuron scores from validation data
    with torch.no_grad():
        v=0
        for item in validation_data:
            textid = item[0]
            text = item[1]
            
            feature_vector = make_feature_vector(text.split(), feature_index)
            hidden, logits, log_probs = model(feature_vector, return_all_layers=True)  
    
            neuron_outputs = logits.data.tolist()[0] #logits
            
            # initialize array if we haven't yet
            if len(validation_rankings) == 0:
                for output in neuron_outputs:
                    validation_rankings.append([0.0] * len(validation_data))
                        
            n=0
            for output in neuron_outputs:
                validation_rankings[n][v] = output
                n += 1
                        
            v += 1
    
    # Step 3: rank-order the validation scores 
    v=0
    for validation in validation_rankings:
        validation.sort() 
        validation_rankings[v] = validation
        v += 1
            

    # Step 3: iterate unlabeled items

    outliers = []
    if limit == -1: # we're drawing from *everything* this will take a while                                               
        print("Get model scores for unlabeled data (this might take a while)")
    else:
        # only apply the model to a limited number of items                                                                            
        shuffle(unlabeled_data)
        unlabeled_data = unlabeled_data[:limit]

    with torch.no_grad():
        for item in unlabeled_data:
            textid = item[0]
            if textid in already_labeled:
                continue

            text = item[1]

            feature_vector = make_feature_vector(text.split(), feature_index)
            hidden, logits, log_probs = model(feature_vector, return_all_layers=True)            
            
            neuron_outputs = logits.data.tolist()[0] #logits
   
            total_rank = 0;
            
            n=0
            ranks = []
            for output in neuron_outputs:
                rank = get_rank(output, validation_rankings[n])
                ranks.append(rank)
                total_rank += rank
                n += 1 
            
            item[3] = "logit_rank_outlier"
            
            item[4] = 1 - (sum(ranks) / len(neuron_outputs)) # average rank
            # TODO add lowest rank
            
            outliers.append(item)
            
    outliers.sort(reverse=True, key=lambda x: x[4])       
    return outliers[:number:]       
            




def evaluate_model(model, evaluation_data):
    """Evaluate the model on the held-out evaluation data

    Return the f-value for disaster-related and the AUC
    """

    related_confs = [] # related items and their confidence of being related
    not_related_confs = [] # not related items and their confidence of being _related_

    true_pos = 0.0 # true positives, etc 
    false_pos = 0.0
    false_neg = 0.0

    with torch.no_grad():
        for item in evaluation_data:
            _, text, label, _, _, = item

            feature_vector = make_feature_vector(text.split(), feature_index)
            log_probs = model(feature_vector)

            # get confidence that item is disaster-related
            prob_related = math.exp(log_probs.data.tolist()[0][1]) 

            if(label == "1"):
                # true label is disaster related
                related_confs.append(prob_related)
                if prob_related > 0.5:
                    true_pos += 1.0
                else:
                    false_neg += 1.0
            else:
                # not disaster-related
                not_related_confs.append(prob_related)
                if prob_related > 0.5:
                    false_pos += 1.0

    # Get FScore
    if true_pos == 0.0:
        fscore = 0.0
    else:
        precision = true_pos / (true_pos + false_pos)
        recall = true_pos / (true_pos + false_neg)
        fscore = (2 * precision * recall) / (precision + recall)

    # GET AUC
    not_related_confs.sort()
    total_greater = 0 # count of how many total have higher confidence
    for conf in related_confs:
        for conf2 in not_related_confs:
            if conf < conf2:
                break
            else:                  
                total_greater += 1


    denom = len(not_related_confs) * len(related_confs) 
    auc = total_greater / denom

    return[fscore, auc]


# TODO DELETE


def get_low_conf_unlabeled(model, unlabeled_data, number=80, limit=100000):
    confidences = []
    if limit == -1: # we're predicting confidence on *everything* this will take a while
        print("Get confidences for unlabeled data (this might take a while)")
    else: 
        # only apply the model to a limited number of items
        shuffle(unlabeled_data)
        unlabeled_data = unlabeled_data[:limit]
    
    with torch.no_grad():
        for item in unlabeled_data:
            textid = item[0]
            if textid in already_labeled:
                continue

            text = item[1]

            feature_vector = make_feature_vector(text.split(), feature_index)
            log_probs = model(feature_vector)

            # get confidence that it is related
            prob_related = math.exp(log_probs.data.tolist()[0][1]) 
            
            if prob_related < 0.5:
                confidence = 1 - prob_related
            else:
                confidence = prob_related 

            item[3] = "low confidence"
            item[4] = confidence
            confidences.append(item)

    confidences.sort(key=lambda x: x[4])
    return confidences[:number:]



if evaluation_count <  minimum_evaluation_items:
    #Keep adding to evaluation data first
    print("Creating evaluation data:\n")

    shuffle(data)
    needed = minimum_evaluation_items - evaluation_count
    data = data[:needed]
    print(str(needed)+" more annotations needed")

    data = get_annotations(data) 
	
    related = []
    not_related = []

    for item in data:
        label = item[2]    
        if label == "1":
            related.append(item)
        elif label == "0":
            not_related.append(item)

    # append evaluation data
    append_data(evaluation_related_data, related)
    append_data(evaluation_not_related_data, not_related)

if validation_count <  minimum_validation_items:
    #Keep adding to evaluation data first
    print("Creating validation data:\n")

    shuffle(data)
    needed = minimum_validation_items - validation_count
    data = data[:needed]
    print(str(needed)+" more annotations needed")

    data = get_annotations(data) 
    
    related = []
    not_related = []

    for item in data:
        label = item[2]    
        if label == "1":
            related.append(item)
        elif label == "0":
            not_related.append(item)

    # append validation data
    append_data(validation_related_data, related)
    append_data(validation_not_related_data, not_related)



elif training_count < minimum_training_items:
    # lets create our first training data! 
    print("Creating initial training data:\n")

    shuffle(data)
    needed = minimum_training_items - training_count
    data = data[:needed]
    print(str(needed)+" more annotations needed")

    data = get_annotations(data)

    related = []
    not_related = []

    for item in data:
        label = item[2]
        if label == "1":
            related.append(item)
        elif label == "0":
            not_related.append(item)

    # append training data
    append_data(training_related_data, related)
    append_data(training_not_related_data, not_related)
else:
    # lets start Active Learning!! 
    print("Sampling via Diversity Learning:\n")

    sampled_data = get_random_items(data, number=5)


    # GET MODEL-BASED OUTLIER SAMPLES
    '''
    print("Sampling Model Outliers\n")
    # train on 90% of the data, hold out 10% for validation
    new_training_data = training_data[:int(len(training_data)*0.9)] 
    validation_data = training_data[len(new_training_data):] 
    
    vocab_size = create_features()
    model_path = train_model(training_data, evaluation_data=evaluation_data, vocab_size=vocab_size)
    model = SimpleTextClassifier(2, vocab_size)
    model.load_state_dict(torch.load(model_path))

    model_outliers = get_model_outliers(model, data, validation_data, number=95)
    sampled_data +=  model_outliers 

    '''

    # GET CLUSTER-BASED SAMPLES
    '''
    print("Sampling via Clustering\n")
    cluster_samples = get_cluster_samples(data, num_clusters=32)
    sampled_data += cluster_samples 
    '''

    # GET REPRESENTATIVE SAMPLES
    '''    
    print("Sampling via Representative Sampling\n")
    representative_samples = get_representative_samples(training_data, data, number=95)
    sampled_data += representative_samples 
    '''

    # GET REPRESENTATIVE SAMPLES USING ADAPTIVE SAMPLING
    '''
    print("Sampling via Adaptive Representative Sampling\n")    
    representative_adaptive_samples = get_adaptive_representative_samples(training_data, data, number=95)
    sampled_data += representative_adaptive_samples 
    '''
    
    shuffle(sampled_data)
    
    sampled_data = get_annotations(sampled_data)
    
    related = []
    not_related = []
    for item in sampled_data:
        label = item[2]
        if label == "1":
            related.append(item)
        elif label == "0":
            not_related.append(item)
        
    # append training data files
    append_data(training_related_data, related)
    append_data(training_not_related_data, not_related)
    

if training_count > minimum_training_items:
    print("\nRetraining model with new data")
    
	# UPDATE OUR DATA AND (RE)TRAIN MODEL WITH NEWLY ANNOTATED DATA
    training_data = load_data(training_related_data) + load_data(training_not_related_data)
    training_count = len(training_data)

    evaluation_data = load_data(evaluation_related_data) + load_data(evaluation_not_related_data)
    evaluation_count = len(evaluation_data)

    vocab_size = create_features()
    model_path = train_model(training_data, evaluation_data=evaluation_data, vocab_size=vocab_size)
    model = SimpleTextClassifier(2, vocab_size)
    model.load_state_dict(torch.load(model_path))

    accuracies = evaluate_model(model, evaluation_data)
    print("[fscore, auc] =")
    print(accuracies)
    print("Model saved to: "+model_path)
    