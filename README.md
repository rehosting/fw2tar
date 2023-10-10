Singularity Unblob 
---

Scripts to run unblob in a singularity container.

Clone repo and build with `./make_container.sh`, then you should get a .sif file in your current directory. Copy that to the environment where you'd like to run it.

Run the singularity container with the following (and put your input in ./input)
```
mkdir input output
singularity exec -B $(pwd)/input:/data/input,$(pwd)/output:/data/output myunblob.sif /unblob/run.sh /data/input/your_fw.bin
```

