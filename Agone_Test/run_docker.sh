#!/bin/bash

# Costruisci l'immagine Docker
docker build -t agone_test_image .

# Esegui il container Docker, montando la directory corrente
docker run --rm -v "$(pwd):/app" agone_test_image