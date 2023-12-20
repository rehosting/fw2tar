Rootfs tarball generator with Unblob 
---

This repository contains a simple container to extract
and tar a linux root filesystem using unblob.

It also includes scripts to build this container as singularity so
it can run in an unprivileged environment.

### Docker
#### Build container
Clone this repo and `cd` into its root then run

```sh
docker build -t unblob .
```

#### Run container
Run the docker container with the following (and put your input in the share directory)
```sh
mkdir share
docker run --rm -it \
	-v $(pwd)/share:/share \
	unblob \
	/share/your_fw.bin
```



### Singularity
#### Build container
Clone this repo and `cd` into its root.

Build `unblob.sif` with `./make_container.sh`, or by running

```sh
docker build -t unblob .

docker run -v /var/run/docker.sock:/var/run/docker.sock \
	-v $(pwd):/output \
	--privileged \
	-t --rm \
	quay.io/singularity/docker2singularity unblob

mv unblob*.sif unblob.sif
```

Copy that to the environment where you'd like to run it.

#### Run container
Run the singularity container with the following (and put your input in the share directory)
```
mkdir share
singularity exec \
	-B $(pwd)/share:/share \
	myunblob.sif \
	/unblob/run.sh /share/your_fw.bin
```

Your filesystem will be created at `/share/your_fw.tar.gz`
