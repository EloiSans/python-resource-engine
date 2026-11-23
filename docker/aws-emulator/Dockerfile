# Official AWS Emulator image recipe, from the AWS Emulator User Guide:
# https://docs.aws.amazon.com/emulator/latest/userguide/getting-started-docker.html
# AWS does not publish a prebuilt image. On arm64 hosts build with
#   --build-arg ARCH=aarch64
FROM public.ecr.aws/amazonlinux/amazonlinux:2023-minimal
ARG ARCH=x86_64
ADD https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS/latest/aws-emulator-${ARCH}.rpm /tmp/aws-emulator.rpm
RUN rpm -i /tmp/aws-emulator.rpm && rm /tmp/aws-emulator.rpm && mkdir -p /home/emulator && chown 1000 /home/emulator
USER 1000
ENV HOME=/home/emulator EMULATOR_BIND_ADDRESS=0.0.0.0
ENTRYPOINT ["aws-emulator"]
