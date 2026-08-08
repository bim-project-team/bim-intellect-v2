## Setup
```bash
git clone https://github.com/bim-project-team/bim-intellect-v2.git
cd bim-intellect-v2
docker-compose up -d                          # starts Neo4j
pip install -r requirements.txt
uvicorn main:app --reload
```
