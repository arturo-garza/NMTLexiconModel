#!/bin/sh
# Distributed under MIT license

# this sample script postprocesses the MT output,
# including merging of BPE subword units,
# detruecasing, and detokenization

script_dir=`dirname $0`
main_dir=$script_dir/../

#language-independent variables (toolkit locations)
#. $main_dir/../vars

#language-dependent variables (source and target language)
. $main_dir/vars

sed -r 's/\@\@ //g' |
$MOSES_SCRIPTS/scripts/recaser/detruecase.perl |
$MOSES_SCRIPTS/scripts/tokenizer/detokenizer.perl -l $trg

