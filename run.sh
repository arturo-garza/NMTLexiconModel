#!/bin/sh


. $main_dir/vars #General environment variables
#. $main_dir/run_scripts/vars #Source and destination language

python $nematus_home/nematus/nmt.py \
--model $main_dir/model/model_test.npz \
--datasets $main_dir/data/test/corpus.$src $main_dir/data/test/corpus.$trg \
--dictionaries $main_dir/data/test/vocab.$src.json $main_dir/data/test/vocab.$trg.json \
--valid_datasets $data_dir/newstest2013.bpe.$src $data_dir/newstest2013.bpe.$trg \
--dim_word 512 \
--dim 1024 \
--lrate 0.0001 \
--optimizer adam \
--maxlen 50 \
--batch_size 32 \
--valid_batch_size 32 \
--validFreq 50 \
--dispFreq 5 \
--saveFreq 50 \
--sampleFreq 50 \
--tie_decoder_embeddings \
--max_epochs 8 \
--layer_normalisation
