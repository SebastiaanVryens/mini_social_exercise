# Mini Social

This is a mock social media platform created for the Social Computing course at the University of Oulu by Dr. Aku Visuri and Daniel Szabo.

The repository contains a ready database, you do not have to create your own data.

## Design Claims

I implemented three features, each based on a Design Claim from Kraut and Resnick, *Building Successful Online Communities: Evidence-Based Social Design* (MIT Press, 2011), Chapter 2 (Encouraging Contribution to Online Communities) and Chapter 3 (Encouraging Commitment in Online Communities).

1. **Ch. 2, Design Claim 4:** "Compared to broadcasting requirements for contribution to all community members, asking specific people to make contributions increases the likelihood that they will do so."

   When a request goes to everyone, each person assumes someone else will answer it (the bystander effect), but a request addressed to one person makes that person feel responsible. The book backs this with a chat room study where requests for help were answered up to 50 percent faster when the person was addressed by name, and the difference grew as more people were present. In Mini Social, you click "Ask someone" on your own post and pick a member, with suggestions of people who have posted about similar topics; that member sees the request in their requests list and can reply or decline.

2. **Ch. 2, Design Claim 23:** "Rewards—whether in the form of status, privileges, or material benefits—motivate contributions."

   People contribute more when contributing earns them something, and privileges also act as a status symbol that tells others "this person is valued here." The book's example is Slashdot, where members earn the right to moderate other people's comments. In Mini Social, you earn reputation points when others react to or comment on your posts, and higher levels unlock new abilities. Reaching Level 2 lets you pin a post to the top of your profile, and reaching Level 3 lets you choose your own avatar color, which everyone sees next to your name on every post and comment.

3. **Ch. 3, Design Claim 14:** "Facilitating interaction with 'friends of friends' can enhance bonds-based commitment."

   Two people who share a friend are likely to get along, and people who form personal bonds on a platform are more likely to keep coming back. The book points to LinkedIn and Facebook, which show you friends of your friends for exactly this reason. In Mini Social, a "People you may know" list shows people followed by the people you follow, with how many connections you share, and you can follow them or click "Not interested" to hide them.

The code for each feature is in its own commented section in `app.py`.

## How to run

1. Make sure you have Python 3.11 installed
2. Install dependencies by running `pip install -r requirements.txt`
3. Start the server by running `python ./app.py`
4. The web application will be available at http://127.0.0.1:8081.
5. The admin interface is at http://127.0.0.1:8081/admin, available only to the Admin user (username: admin, pw: admin)

### Docker

If you prefer to run Mini-Social as a docker container, you can build the image and run it as follows:

1. Build image with `docker build --tag mini-social .`
2. Run image with the following command, where 8000 is the port you will access the application at `http://127.0.0.1:8000`

```
docker run -d -p 8000:5000 \
    --restart always \
    -v $(pwd)/database.sqlite:/python-docker/database.sqlite \
    mini-social
```
