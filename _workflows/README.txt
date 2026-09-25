publish.yml and stats.yml replace the files of the same name in .github\workflows\
(Claude's file tools can't write into that folder). In a terminal opened in
C:\Code\Claude\gurukul-publisher run:

    move /y _workflows\*.yml .github\workflows\
    rmdir /s /q _workflows
