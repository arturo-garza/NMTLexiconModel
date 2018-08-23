# NMTLexiconModel

Modified version of the Nematus framework (https://github.com/EdinburghNLP/nematus/) to implement a lexicon model as proposed in https://arxiv.org/abs/1710.01329.

 ## nematus_lexical 
 Contains the nematus code with the changes to run the framework with the lexical model
    - 
    Changes:
            model.py 
                - Added lexical model
                - Decoder
                    Modified the decoder initialiser to receive the lexical model in its input.
                    Modified score funciton to retrieve from the RecursiveLayer the c_embed
                    Modified the sample function to calculate the logits with the lexical model when the --lexical option is enabled.
                - Predictor 
                    Modified the get_logits funtion to 
              layers.py 
                -Modified AttentionStep class' forward function to return the c_embed if the source embeddings are given in the input.
              nmt.py 
                - Added the following options
                    --lexical Boolean option to train the architecture with the lexical model
                    --bilingual_pretrain Boolean option to specify that we want to perform pretrain on a bilingual dictionary
                    --pretrain_dictionary_src If  --bilingual_pretrain is enable this option is required to specify the source file of the parallel dictionationary
                    --pretrain_dictionary_trg  If  --bilingual_pretrain is enable this option is required to specify the target file of the parallel dictionationary
                    --pretrain_vocabs Option to provide the vocabularies required by the TextIterator class in Nematus
              inference.py
                -Modified the construct_beam_search_functions to to calculate the logits with the lexical model when the --lexical option is enabled.

 ## nematus_weighted
 Contains the nematus code with the additional changes required to run a fixed weight version of the lexical model.
    -
    Changes:
        - Decoder
            Modified score function
        - Predictor
    

 ## scripts - Contains auxiliary code we used to run our experiments
 
    - Create Bilingual dictionaries - folder that contains the sripts we built to create the bilingual dictionary and then the second script used to extract the 400 entries we used to transalate. <br />
        - process_dict.sh: File to run in order to obtain the parallel dictionary from the phrase table in the Valhalla cluster. <br />
    
 
## run_scripts 
Example of the script we used to train the architecture with the lexical model. As examples provided are: <br />
        - wmt14_shallow_lexical -  Script to run the model with the lexical model by only using the new --lexical option <br />
        - europarl_lexical_pretrain - Script to run the model with the lexical model and the pretrain using the pre-train options <br />

## validate_scripts
This folder contains the scripts we used to get translations from our models. As example we proveide the following: <br />
        - preprocess_validate_europarl_lexical_v2_progress - is a shell script that creates multiple copies of the model's json. This is done to be able to iterate through all of the intermediate models. <br />
        - validate_europarl_shallow_lexical_progress_newstest2014 - is a shell script that iterates over all the saved models. It creates a translation file and their BLEU score for each of them.  <br />

vars - scpecifies all the directories we used in the Valhalla cluster at the University of Edinburgh. <br />


To run this version of the Nematus framework with the lexical model the following options are available: <br />

   --lexical - Boolean flag that specifies that the framework should be trained with the lexical model <br />
   --bilingual_pretrain Boolean option to specify that we want to perform pretrain on a bilingual dictionary <br />
   --pretrain_dictionary_src If  --bilingual_pretrain is enable this option is required to specify the source file of the parallel dictionationary <br />
   --pretrain_dictionary_trg  If  --bilingual_pretrain is enable this option is required to specify the target file of the parallel dictionationary <br />
   --pretrain_vocabs Option to provide the vocabularies required by the TextIterator class in Nematus <br />
   


## Options

 --lexical  :  To add train using the Lexical model <br />
 --bilingual_pretrain  :  To add train using the Lexical model <br />
