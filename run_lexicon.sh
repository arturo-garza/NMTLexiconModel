#!/bin/sh


. $main_dir/vars #General environment variables
#. $main_dir/run_scripts/vars #Source and destination language
#--datasets $main_dir/ted_data/ted-corpus.bpe.$src $main_dir/ted_data/ted-corpus.bpe.$trg \ --dictionaries $main_dir/ted_data/ted-corpus.bpe.$src.json $main_dir/ted_data/ted-corpus.bpe.$trg.json \

python $nematus_home/nematus/nmt.py \
--model $main_dir/model/model_test.npz \
--datasets $main_dir/data/test/corpus.$src $main_dir/data/test/corpus.$trg \
--valid_datasets $data_dir/newstest2013.bpe.$src $data_dir/newstest2013.bpe.$trg \
--dictionaries $main_dir/data/test/vocab.$src.json $main_dir/data/test/vocab.$trg.json \
--dim_word 512 \
--dim 1024 \
--lrate 0.0001 \
--optimizer adam \
--maxlen 50 \
--batch_size 32 \
--valid_batch_size 32 \
--validFreq 1000 \
--dispFreq 500 \
--saveFreq 1000 \
--sampleFreq 1000 \
--tie_decoder_embeddings \
--layer_normalisation \
--lexical
